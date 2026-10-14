from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..config import LLMConfig

logger = logging.getLogger(__name__)


class AIServiceError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502, code: str = "provider_error"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def _endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


def _provider_error(status_code: int) -> AIServiceError:
    if status_code in {401, 403}:
        return AIServiceError("AI 服务认证失败，请检查服务端 API Key。", 502, "authentication_failed")
    if status_code == 404:
        return AIServiceError("AI 模型或接口地址不存在，请检查服务端配置。", 502, "model_or_endpoint_not_found")
    if status_code == 429:
        return AIServiceError("AI 服务请求过于频繁或额度不足，请稍后重试。", 503, "rate_limited")
    if status_code >= 500:
        return AIServiceError("上游 AI 服务暂时不可用，请稍后重试。", 503, "upstream_unavailable")
    return AIServiceError("AI 服务拒绝了本次请求，请检查服务端配置。", 502, "provider_rejected")


class AIService:
    def __init__(self, config: LLMConfig, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        self.transport = transport

    def ensure_available(self) -> None:
        if not self.config.enabled:
            raise AIServiceError("AI 辅助研判未启用。", 503, "disabled")
        if not self.config.configured:
            raise AIServiceError("AI 辅助研判配置不完整，请在系统设置中完成配置。", 503, "not_configured")

    def _request(self, messages: list[dict[str, str]], stream: bool) -> tuple[str, dict[str, str], dict[str, Any]]:
        self.ensure_available()
        assert self.config.api_key is not None
        headers = {
            "Authorization": f"Bearer {self.config.api_key.get_secret_value()}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
        }
        payload = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_output_tokens,
            "stream": stream,
        }
        return _endpoint(self.config.base_url), headers, payload

    async def complete(self, messages: list[dict[str, str]]) -> str:
        url, headers, payload = self._request(messages, False)
        timeout = httpx.Timeout(self.config.timeout)
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:
                    response = await client.post(url, headers=headers, json=payload)
                if response.status_code >= 400:
                    error = _provider_error(response.status_code)
                    if attempt == 0 and response.status_code >= 500:
                        await asyncio.sleep(0.15)
                        continue
                    raise error
                content = response.json()["choices"][0]["message"]["content"]
                if not isinstance(content, str) or not content.strip():
                    raise AIServiceError("AI 服务返回了空内容。", 502, "empty_response")
                return content
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt == 0:
                    await asyncio.sleep(0.15)
                    continue
                logger.warning("AI provider transport failure: %s", type(exc).__name__)
                raise AIServiceError("连接 AI 服务超时或网络不可用，请稍后重试。", 503, "transport_error") from exc
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise AIServiceError("AI 服务响应格式不兼容。", 502, "invalid_response") from exc
        raise AIServiceError("AI 服务暂时不可用。", 503, "upstream_unavailable")

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        url, headers, payload = self._request(messages, True)
        timeout = httpx.Timeout(self.config.timeout)
        yielded_any = False
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:
                    async with client.stream("POST", url, headers=headers, json=payload) as response:
                        if response.status_code >= 400:
                            raise _provider_error(response.status_code)
                        yielded_this_attempt = False
                        async for line in response.aiter_lines():
                            line = line.strip()
                            if not line or line.startswith(":"):
                                continue
                            raw = line[5:].strip() if line.startswith("data:") else line
                            if raw == "[DONE]":
                                break
                            try:
                                data = json.loads(raw)
                                choices = data.get("choices") if isinstance(data, dict) else None
                                # 兼容 usage-only、reasoning-only 和 finish_reason 空块。
                                # 这些都是合法的 OpenAI-compatible 流事件，不应中断正文。
                                if not isinstance(choices, list) or not choices:
                                    continue
                                choice = choices[0] if isinstance(choices[0], dict) else {}
                                delta = choice.get("delta", {}).get("content")
                                if delta is None:
                                    delta = choice.get("message", {}).get("content")
                            except (json.JSONDecodeError, TypeError, AttributeError) as exc:
                                raise AIServiceError("AI 服务流式响应格式不兼容。", 502, "invalid_stream") from exc
                            if isinstance(delta, str) and delta:
                                yielded_this_attempt = True
                                yielded_any = True
                                yield delta
                        if not yielded_this_attempt:
                            raise AIServiceError("AI 服务返回了空内容。", 502, "empty_response")
                        return
            except AIServiceError as exc:
                if attempt == 0 and not yielded_any and exc.code == "upstream_unavailable":
                    await asyncio.sleep(0.15)
                    continue
                raise
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt == 0 and not yielded_any:
                    await asyncio.sleep(0.15)
                    continue
                logger.warning("AI provider stream failure: %s", type(exc).__name__)
                raise AIServiceError("连接 AI 服务超时或网络不可用，请稍后重试。", 503, "transport_error") from exc


def public_status(config: LLMConfig) -> dict[str, Any]:
    return {
        "enabled": config.enabled,
        "configured": config.configured,
        "available": config.configured,
        "key_configured": bool(config.api_key and config.api_key.get_secret_value().strip()),
        "model": config.model or None,
        "timeout_seconds": config.timeout,
        "temperature": config.temperature,
        "streaming": True,
        "message": (
            "智盾助手已就绪" if config.configured
            else "AI 辅助研判未启用" if not config.enabled
            else "AI 辅助研判配置不完整，请前往系统设置检查 AI 配置"
        ),
    }
