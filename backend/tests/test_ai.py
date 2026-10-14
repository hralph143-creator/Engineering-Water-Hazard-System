import asyncio
import json
from datetime import datetime, timezone

import httpx
import numpy as np
import pytest
import rasterio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.api import ai as ai_api
from backend.app.api.ai import _task_messages
from backend.app import config as config_module
from backend.app.config import LLMConfig, get_llm_config, save_local_llm_settings
from backend.app.db.database import Base
from backend.app.db.database import get_db
from backend.app.db.models import DatasetRecord, InferenceResult, InferenceTask
from backend.app.schemas.ai import AITaskRequest
from backend.app.services.ai_context import AIContextError, build_compare_context, build_task_context
from backend.app.services.ai_service import AIService, AIServiceError, public_status
from backend.app.services.secret_store import protect_secret, unprotect_secret


@pytest.fixture(autouse=True)
def isolated_llm_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOODRISK_LLM_CONFIG_PATH", str(tmp_path / "llm.local.json"))


def _database(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'ai.db').as_posix()}")
    Base.metadata.create_all(engine)
    return engine


def _seed_completed(db: Session, tmp_path, suffix="a", flood_ratio=0.25):
    mask = tmp_path / f"mask-{suffix}.tif"
    values = np.array([[0, 1], [1 if suffix == "b" else 0, 1]], dtype=np.uint8)
    with rasterio.open(mask, "w", driver="GTiff", width=2, height=2, count=1,
                       dtype="uint8", crs="EPSG:4326",
                       transform=rasterio.Affine(1, 0, 0, 0, -1, 2)) as dst:
        dst.write(values, 1)
    dataset = DatasetRecord(
        id=f"d-{suffix}", name=f"scene-{suffix}.tif", path=str(tmp_path / f"source-{suffix}.tif"),
        width=2, height=2, bands=2, dtype="float32", crs="EPSG:4326",
        transform="[]", bounds="[]", vv_stats='{"mean": -12.5}', vh_stats='{"mean": -19.2}',
    )
    task = InferenceTask(
        id=f"t-{suffix}", dataset=dataset, status="completed", threshold=0.5,
        checkpoint="C:/secret/model.pt", model_name="U-Net",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        finished_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    result = InferenceResult(
        id=f"r-{suffix}", task=task, probability_path=str(tmp_path / "private-prob.tif"),
        mask_path=str(mask), comparison_path=str(tmp_path / "private-comparison.png"),
        flood_pixel_count=int(flood_ratio * 100), valid_pixel_count=100,
        flood_ratio=flood_ratio, mean_confidence=0.88, risk_level="中风险",
        inference_seconds=1.5, probability_min=0.1, probability_max=0.95,
        probability_mean=0.4, probability_median=0.35,
        high_confidence_flood_pixel_count=12, high_confidence_flood_ratio=0.12,
    )
    db.add_all([dataset, task, result]); db.commit()
    return task, result, mask


def test_unconfigured_status_does_not_leak_key(monkeypatch):
    for name in ["LLM_ENABLED", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"]:
        monkeypatch.delenv(name, raising=False)
    status = public_status(get_llm_config())
    assert status["enabled"] is False and status["available"] is False
    assert "api_key" not in status and "base_url" not in status


def test_status_with_key_only_returns_boolean():
    cfg = LLMConfig(enabled=True, api_key=SecretStr("top-secret-key"), model="model-x")
    encoded = json.dumps(public_status(cfg), ensure_ascii=False)
    assert "top-secret-key" not in encoded
    assert json.loads(encoded)["key_configured"] is True


def test_windows_secret_round_trip_does_not_contain_plaintext():
    secret = "local-test-secret"
    protected = protect_secret(secret)
    assert secret.encode() not in protected
    assert unprotect_secret(protected) == secret


def test_local_settings_take_precedence_over_environment(monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("LLM_API_KEY", "environment-secret")
    monkeypatch.setenv("LLM_BASE_URL", "https://environment.example/v1")
    monkeypatch.setenv("LLM_MODEL", "environment-model")
    save_local_llm_settings(
        enabled=False, base_url="https://local.example/v1", model="local-model",
        timeout=30, temperature=0.1, max_output_tokens=512, api_key="local-secret",
    )
    cfg = get_llm_config()
    assert cfg.enabled is False
    assert cfg.base_url == "https://local.example/v1" and cfg.model == "local-model"
    assert cfg.api_key and cfg.api_key.get_secret_value() == "local-secret"


def test_undecryptable_local_key_does_not_fall_back_to_environment(tmp_path, monkeypatch):
    path = tmp_path / "llm.local.json"
    path.write_text(json.dumps({
        "enabled": False, "base_url": "https://local.example/v1", "model": "local-model",
        "timeout": 60, "temperature": 0.2, "max_output_tokens": 1600,
        "api_key_protected": "ZmFrZQ==",
    }), encoding="utf-8")
    monkeypatch.setenv("FLOODRISK_LLM_CONFIG_PATH", str(path))
    monkeypatch.setenv("LLM_API_KEY", "wrong-environment-secret")
    monkeypatch.setattr(config_module, "unprotect_secret", lambda value: (_ for _ in ()).throw(config_module.SecretStoreError("controlled")))
    cfg = get_llm_config()
    assert cfg.model == "local-model"
    assert cfg.api_key is None


def test_task_context_not_found(tmp_path):
    engine = _database(tmp_path)
    with Session(engine) as db, pytest.raises(AIContextError) as raised:
        build_task_context(db, "missing")
    assert raised.value.status_code == 404


def test_unfinished_task_context_is_rejected(tmp_path):
    engine = _database(tmp_path)
    with Session(engine) as db:
        dataset = DatasetRecord(id="pending-d", name="pending.tif", path="pending.tif",
                                width=1, height=1, bands=2, dtype="float32")
        task = InferenceTask(id="pending-t", dataset=dataset, status="pending", threshold=0.5,
                             checkpoint="model.pt", model_name="U-Net")
        db.add_all([dataset, task]); db.commit()
        with pytest.raises(AIContextError) as raised:
            build_task_context(db, "pending-t")
    assert raised.value.status_code == 409


def test_ai_http_status_and_missing_task(tmp_path, monkeypatch):
    engine = _database(tmp_path)
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("LLM_API_KEY", "http-test-secret")
    monkeypatch.setenv("LLM_MODEL", "mock")

    def override_db():
        with Session(engine) as db:
            yield db

    test_app = FastAPI()
    test_app.include_router(ai_api.router, prefix="/api")
    test_app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(test_app)
        status = client.get("/api/ai/status")
        assert status.status_code == 200 and status.json()["data"]["available"] is True
        assert "http-test-secret" not in status.text
        missing = client.post("/api/ai/analyze/stream", json={"task_id": "missing", "question": "分析"})
        assert missing.status_code == 404
        assert missing.json()["detail"] == "任务不存在"
    finally:
        test_app.dependency_overrides.clear()


def test_completed_task_context_is_structured_and_has_no_paths(tmp_path):
    engine = _database(tmp_path)
    with Session(engine) as db:
        _seed_completed(db, tmp_path)
        context = build_task_context(db, "t-a")
    encoded = json.dumps(context, ensure_ascii=False)
    assert context["result"]["flood_ratio"] == pytest.approx(0.25)
    assert context["dataset"]["vv_stats"]["mean"] == pytest.approx(-12.5)
    assert "C:/secret" not in encoded and "private-prob" not in encoded
    assert "rainfall" in context["unavailable_fields"]


def test_prompt_contains_safety_rules_and_real_context(tmp_path):
    engine = _database(tmp_path)
    with Session(engine) as db:
        _seed_completed(db, tmp_path)
        context = build_task_context(db, "t-a")
    messages = _task_messages(AITaskRequest(task_id="t-a", question="解读结果"), context)
    combined = "\n".join(item["content"] for item in messages)
    assert "不得虚构降雨量" in combined
    assert '"flood_ratio":0.25' in combined
    assert "C:/secret/model.pt" not in combined
    assert "\n### " not in combined
    assert "一、综合判断" in combined and "四、复核优先级" in combined


def test_chat_mode_uses_direct_answer_guide(tmp_path):
    engine = _database(tmp_path)
    with Session(engine) as db:
        _seed_completed(db, tmp_path)
        context = build_task_context(db, "t-a")
    messages = _task_messages(AITaskRequest(
        task_id="t-a", mode="chat", question="哪些结论需要现场复核？",
        existing_analysis="一、综合判断\n这是用户已经看过的完整分析。",
    ), context)
    combined = "\n".join(item["content"] for item in messages)
    assert "直接回答最后一个问题" in combined
    assert "不是再写一份专业分析" in combined
    assert "用户已经看过的完整分析" in combined
    assert "不得复制其句子、结构或大段结论" in combined
    assert "\n### " not in combined


def test_unconfigured_service_fails_cleanly():
    service = AIService(LLMConfig(enabled=True, model=""))
    with pytest.raises(AIServiceError) as raised:
        service.ensure_available()
    assert raised.value.code == "not_configured"


@pytest.mark.parametrize("config", [
    LLMConfig(enabled=True, api_key=None, model="mock"),
    LLMConfig(enabled=True, api_key=SecretStr("test"), base_url="", model="mock"),
    LLMConfig(enabled=True, api_key=SecretStr("test"), model=""),
])
def test_incomplete_configurations_fail_cleanly(config):
    with pytest.raises(AIServiceError) as raised:
        AIService(config).ensure_available()
    assert raised.value.code == "not_configured"


def test_openai_compatible_complete_and_auth_error():
    cfg = LLMConfig(enabled=True, api_key=SecretStr("test-key"), model="mock-model")
    ok_transport = httpx.MockTransport(lambda request: httpx.Response(
        200, json={"choices": [{"message": {"content": "受控分析"}}]}, request=request
    ))
    assert asyncio.run(AIService(cfg, ok_transport).complete([{"role": "user", "content": "test"}])) == "受控分析"
    denied = httpx.MockTransport(lambda request: httpx.Response(401, json={"error": "denied"}, request=request))
    with pytest.raises(AIServiceError) as raised:
        asyncio.run(AIService(cfg, denied).complete([{"role": "user", "content": "test"}]))
    assert raised.value.code == "authentication_failed"


@pytest.mark.parametrize(("status", "code"), [
    (403, "authentication_failed"),
    (404, "model_or_endpoint_not_found"),
    (429, "rate_limited"),
    (500, "upstream_unavailable"),
    (502, "upstream_unavailable"),
])
def test_provider_http_errors_are_mapped(status, code):
    cfg = LLMConfig(enabled=True, api_key=SecretStr("test-key"), model="mock-model")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status, json={"error": "controlled"}, request=request)
    )
    with pytest.raises(AIServiceError) as raised:
        asyncio.run(AIService(cfg, transport).complete([{"role": "user", "content": "test"}]))
    assert raised.value.code == code


@pytest.mark.parametrize("response", [
    lambda request: httpx.Response(200, json={"choices": [{"message": {"content": ""}}]}, request=request),
    lambda request: httpx.Response(200, text="not-json", request=request),
])
def test_empty_or_non_json_response_is_safe(response):
    cfg = LLMConfig(enabled=True, api_key=SecretStr("test-key"), model="mock-model")
    with pytest.raises(AIServiceError):
        asyncio.run(AIService(cfg, httpx.MockTransport(response)).complete(
            [{"role": "user", "content": "test"}]
        ))


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout])
def test_transport_failures_are_safe(error_type):
    cfg = LLMConfig(enabled=True, api_key=SecretStr("test-key"), model="mock-model")

    def fail(request):
        raise error_type("controlled failure", request=request)

    with pytest.raises(AIServiceError) as raised:
        asyncio.run(AIService(cfg, httpx.MockTransport(fail)).complete(
            [{"role": "user", "content": "test"}]
        ))
    assert raised.value.code == "transport_error"


def test_openai_compatible_stream():
    cfg = LLMConfig(enabled=True, api_key=SecretStr("test-key"), model="mock-model")
    body = 'data: {"choices":[{"delta":{"content":"第一段"}}]}\n\n' \
           'data: {"choices":[{"delta":{"content":"第二段"}}]}\n\n' \
           'data: [DONE]\n\n'
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body, request=request))

    async def collect():
        return [chunk async for chunk in AIService(cfg, transport).stream([{"role": "user", "content": "test"}])]

    assert asyncio.run(collect()) == ["第一段", "第二段"]


def test_stream_ignores_reasoning_finish_and_usage_chunks():
    cfg = LLMConfig(enabled=True, api_key=SecretStr("test-key"), model="mock-model")
    body = '\n\n'.join([
        'data: {"choices":[{"delta":{"reasoning_content":"内部推理"},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{"content":"合规正文"},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
        'data: {"choices":[],"usage":{"total_tokens":20}}',
        'data: [DONE]',
        '',
    ])
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=body, request=request))

    async def collect():
        return [chunk async for chunk in AIService(cfg, transport).stream([{"role": "user", "content": "test"}])]

    assert asyncio.run(collect()) == ["合规正文"]


def test_interrupted_stream_reports_transport_error_after_partial_content():
    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
            raise httpx.ReadError("controlled interruption")

    cfg = LLMConfig(enabled=True, api_key=SecretStr("test-key"), model="mock-model")
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, stream=BrokenStream(), request=request)
    )

    async def collect():
        chunks = []
        with pytest.raises(AIServiceError) as raised:
            async for chunk in AIService(cfg, transport).stream([{"role": "user", "content": "test"}]):
                chunks.append(chunk)
        return chunks, raised.value.code

    assert asyncio.run(collect()) == (["partial"], "transport_error")


def test_compare_context_reads_results_without_modifying_masks(tmp_path):
    engine = _database(tmp_path)
    with Session(engine) as db:
        _, _, mask_a = _seed_completed(db, tmp_path, "a", 0.25)
        _, _, mask_b = _seed_completed(db, tmp_path, "b", 0.5)
        before = (mask_a.read_bytes(), mask_b.read_bytes())
        context = build_compare_context(db, ["r-a", "r-b"])
        after = (mask_a.read_bytes(), mask_b.read_bytes())
    assert before == after
    assert context["differences_b_minus_a"]["flood_ratio"] == pytest.approx(0.25)
    assert context["spatial_consistent"] is True
    assert context["pixel_change_statistics"]["counts"]["new_flood_pixels"] >= 0


def test_completed_task_without_result_is_rejected(tmp_path):
    engine = _database(tmp_path)
    with Session(engine) as db:
        dataset = DatasetRecord(id="empty-d", name="empty.tif", path="empty.tif",
                                width=1, height=1, bands=2, dtype="float32")
        task = InferenceTask(id="empty-t", dataset=dataset, status="completed", threshold=0.5,
                             checkpoint="model.pt", model_name="U-Net")
        db.add_all([dataset, task]); db.commit()
        with pytest.raises(AIContextError) as raised:
            build_task_context(db, "empty-t")
    assert raised.value.status_code == 409


def test_spatial_mismatch_only_allows_statistical_comparison(tmp_path):
    engine = _database(tmp_path)
    with Session(engine) as db:
        _seed_completed(db, tmp_path, "a", 0.25)
        _, result_b, mask_b = _seed_completed(db, tmp_path, "b", 0.5)
        values = np.zeros((2, 2), dtype=np.uint8)
        with rasterio.open(mask_b, "w", driver="GTiff", width=2, height=2, count=1,
                           dtype="uint8", crs="EPSG:4326",
                           transform=rasterio.Affine(1, 0, 100, 0, -1, 2)) as dst:
            dst.write(values, 1)
        result_b.mask_path = str(mask_b); db.commit()
        context = build_compare_context(db, ["r-a", "r-b"])
    assert context["spatial_consistent"] is False
    assert context["pixel_change_statistics"] is None
    assert "仅可进行统计对比" in context["limitation"]
