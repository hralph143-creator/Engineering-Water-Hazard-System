from contextlib import asynccontextmanager
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .api import ai, assistance, change_analyses, compare, dashboard, data_exports, datasets, equipment, forecast, inference, model_info, rainfall, reports, results, risk_assessments, settings, spatial, system, tasks, terrain, uav
from .config import get_config
from .db.database import init_db
from .services.model_service import ModelUnavailableError, init_model_service
from .services.task_queue import task_queue
from .services.runtime_service import configure_logging

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _app.state.database_status = "ok"
    _app.state.database_error = ""
    try:
        init_db()
        logger.info("数据库初始化与幂等迁移完成")
    except Exception as exc:
        _app.state.database_status = "database_error"
        _app.state.database_error = type(exc).__name__
        logger.exception("数据库初始化失败")
    try:
        init_model_service(get_config())
        logger.info("模型服务初始化完成")
    except ModelUnavailableError as exc:
        # 模型文件缺失 / CUDA 不可用时以降级模式启动：
        # 系统其余功能（数据管理、历史、设置、对比、报告）保持可用，
        # 仅识别接口返回 503 明确提示，前端展示"模型不可用"。
        logger.error("模型加载失败，系统以降级模式启动：%s", exc)
    if _app.state.database_status == "ok":
        task_queue.start()
    try:
        yield
    finally:
        if _app.state.database_status == "ok":
            task_queue.stop()


app = FastAPI(
    title="工程水害管理系统 API",
    version="正式版", lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(HTTPException)
async def http_error(_request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"success": False, "data": None, "message": str(exc.detail)})


@app.exception_handler(ModelUnavailableError)
async def model_error(_request: Request, exc: ModelUnavailableError):
    return JSONResponse(status_code=503, content={"success": False, "data": None, "message": str(exc)})


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError):
    details = "; ".join(error["msg"] for error in exc.errors())
    return JSONResponse(status_code=422, content={"success": False, "data": None, "message": f"请求参数无效：{details}"})


@app.exception_handler(Exception)
async def unexpected_error(_request: Request, exc: Exception):
    # 服务端保留完整异常日志，用户侧仅返回摘要，不暴露本地路径与内部堆栈
    logger.exception("unhandled error", exc_info=exc)
    return JSONResponse(status_code=500, content={"success": False, "data": None, "message": "服务器内部错误，请查看服务端日志"})


@app.get("/api/health")
def health():
    status = getattr(app.state, "database_status", "starting")
    return {"success": status == "ok", "data": {"status": status}, "message": "" if status == "ok" else "数据库初始化异常，请导出系统诊断报告"}


for module in (dashboard, datasets, equipment, terrain, rainfall, uav, assistance, forecast, inference, model_info, results, settings, data_exports, tasks, compare, change_analyses, risk_assessments, spatial, reports, ai, system):
    app.include_router(module.router, prefix="/api")


# 桌面发行版与生产部署共用的 SPA 静态入口。开发环境仍可使用 Vite 5173。
_frontend_dir = Path(os.environ.get(
    "FLOODRISK_FRONTEND_DIR",
    Path(__file__).resolve().parents[2] / "frontend" / "dist",
))


@app.get("/{full_path:path}", include_in_schema=False)
def frontend_app(full_path: str):
    if full_path.startswith("api/") or not _frontend_dir.is_dir():
        raise HTTPException(status_code=404, detail="资源不存在")
    candidate = (_frontend_dir / full_path).resolve()
    try:
        candidate.relative_to(_frontend_dir.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="资源不存在") from exc
    if candidate.is_file():
        return FileResponse(candidate)
    index = _frontend_dir / "index.html"
    if index.is_file():
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="前端资源未构建")
