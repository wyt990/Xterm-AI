from fastapi import APIRouter, HTTPException

from core import APP_PASSWORD, DebugAuthFailureModel, LoginModel, create_access_token
from logger import app_logger


router = APIRouter()


@router.get("/api/ping")
async def ping():
    """无鉴权：诊断前端能否连通后端"""
    return {"ok": True}


@router.post("/api/debug/auth_failure")
async def debug_auth_failure(data: DebugAuthFailureModel):
    """无鉴权：前端 401 时上报触发路径，便于排查登录循环"""
    app_logger.info("鉴权", f"[前端上报] 401 来自: {data.url or '?'}")


@router.post("/api/login", responses={401: {"description": "密码错误"}})
async def login(data: LoginModel):
    if data.password == APP_PASSWORD:
        token = create_access_token()
        app_logger.info("鉴权", "登录成功，已颁发 Token")
        return {"access_token": token, "token_type": "bearer"}
    app_logger.info("鉴权", "登录失败：密码错误")
    raise HTTPException(status_code=401, detail="密码错误")
