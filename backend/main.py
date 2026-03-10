import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

# 兼容 PyInstaller 打包：使用 main_desktop 注入的路径
_BASE_DIR = os.getenv("BUNDLE_PATH") or os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
_ENV_PATH = os.getenv("ENV_PATH")

# 加载配置（打包模式用 ENV_PATH，开发模式用 ../.env）
if _ENV_PATH and os.path.exists(_ENV_PATH):
    load_dotenv(dotenv_path=_ENV_PATH)
else:
    load_dotenv(dotenv_path=os.path.join(_BASE_DIR, ".env"))

from core import APP_PASSWORD, FRONTEND_DIR, JWT_SECRET, STATIC_DIR
from logger import app_logger
from routers.ai import router as ai_router
from routers.auth import router as auth_router
from routers.servers import router as servers_router
from routers.sftp import router as sftp_router
from routers.system import router as system_router
from routers.ws import router as ws_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动时写入日志，确保 xterm.log 被创建"""
    try:
        port = os.getenv("SERVER_PORT", "?")
        app_logger.info("系统", f"后端服务已启动，端口 {port}")
        app_logger.info(
            "系统",
            f"JWT_SECRET 已加载: {'是' if JWT_SECRET else '否'}, APP_PASSWORD 已设置: {'是' if APP_PASSWORD else '否'}",
        )
        app_logger.info(
            "系统", f"ENV_PATH={_ENV_PATH}, DB_PATH={os.getenv('DB_PATH', '?')}"
        )
        try:
            _dp = os.getenv("DB_PATH") or ""
            app_logger.info("系统", f"数据库文件存在: {os.path.exists(_dp)}")
        except Exception:
            pass
    except Exception:
        pass
    yield


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def log_api_requests(request: Request, call_next):
    """记录关键请求：页面加载、所有 API，便于诊断前后端是否连通"""
    path = request.url.path
    is_api = path.startswith("/api/")
    is_page = path in ("/", "/frontend/index.html")
    if not is_api and not is_page:
        return await call_next(request)
    try:
        response = await call_next(request)
        try:
            if is_api:
                code = response.status_code
                app_logger.info(
                    "API",
                    f"{request.method} {path}" + (f" -> {code}" if code >= 400 else ""),
                )
            else:
                app_logger.info("系统", f"页面请求: {path} -> {response.status_code}")
        except Exception:
            pass
        return response
    except HTTPException as e:
        if is_api:
            app_logger.info("API", f"{request.method} {path} -> {e.status_code}")
        raise


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """确保异常统一返回 JSON，避免前端解析 HTML 失败"""
    if isinstance(exc, HTTPException):
        if exc.status_code in (401, 403):
            app_logger.info("鉴权", f"[{exc.status_code}] path={request.url.path} detail={exc.detail}")
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    app_logger.info("系统", f"未捕获异常: {exc}")
    return JSONResponse(status_code=500, content={"detail": str(exc) or "服务器内部错误"})


app.mount("/frontend", StaticFiles(directory=FRONTEND_DIR), name="frontend")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(auth_router)
app.include_router(system_router)
app.include_router(servers_router)
app.include_router(ai_router)
app.include_router(sftp_router)
app.include_router(ws_router)


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("SERVER_HOST", "0.0.0.0")
    port = int(os.getenv("SERVER_PORT", 8000))
    uvicorn.run(app, host=host, port=port)
