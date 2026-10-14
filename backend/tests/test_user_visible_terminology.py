from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BANNED = ("城市洪涝", "城市防汛", "抑洪", "FloodRiskSystem")


def _assert_clean(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    found = [term for term in BANNED if term in content]
    assert not found, f"{path.relative_to(ROOT)} 仍包含旧用户可见术语：{found}"


def test_frontend_user_visible_brand_and_copy_are_migrated():
    files = [ROOT / "frontend" / "index.html", ROOT / "frontend" / "src" / "router" / "index.ts"]
    files.extend((ROOT / "frontend" / "src" / "views").glob("*.vue"))
    files.append(ROOT / "frontend" / "src" / "layouts" / "AppLayout.vue")
    for path in files:
        _assert_clean(path)


def test_report_and_ai_user_outputs_are_migrated():
    for relative in (
        "backend/app/services/report_service.py",
        "backend/app/services/ai_prompts.py",
        "backend/app/services/ai_service.py",
        "backend/app/main.py",
    ):
        _assert_clean(ROOT / relative)


def test_primary_documents_are_migrated():
    for name in ("README.md", "系统功能说明.md", "SYSTEM_GUIDE.md"):
        _assert_clean(ROOT / name)


def test_internal_compatibility_names_remain_available():
    models = (ROOT / "backend" / "app" / "db" / "models.py").read_text(encoding="utf-8")
    predictor = (ROOT / "src" / "inference" / "predictor.py").read_text(encoding="utf-8")
    assert "flood_ratio" in models
    assert "flood_pixel_count" in models
    assert "class FloodPredictor" in predictor
