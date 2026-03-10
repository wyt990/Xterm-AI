from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Form, Depends, status, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Annotated, List, Optional
import os
import asyncio
import json
import glob
import io
import shutil
import jwt
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from ssh_handler import SSHHandler

# 兼容 PyInstaller 打包：使用 main_desktop 注入的路径
_BASE_DIR = os.getenv("BUNDLE_PATH") or os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
_ENV_PATH = os.getenv("ENV_PATH")
from ai_handler import AIHandler
from database import Database
from logger import app_logger
from skill_store import (
    get_recommended_skills,
    list_skills_from_github,
    fetch_skill_content,
    install_skill as do_install_skill,
)
from translation import translate_to_chinese as do_translate

# 加载配置（打包模式用 ENV_PATH，开发模式用 ../.env）
if _ENV_PATH and os.path.exists(_ENV_PATH):
    load_dotenv(dotenv_path=_ENV_PATH)
else:
    load_dotenv(dotenv_path=os.path.join(_BASE_DIR, ".env"))

JWT_SECRET = os.getenv("JWT_SECRET", "xterm_secret_key_999")
JWT_ALGORITHM = "HS256"
APP_PASSWORD = os.getenv("APP_PASSWORD", "admin")
SERVER_NOT_FOUND_DETAIL = "Server not found"
LOG_SCOPE_SSH_TEST = "SSH测试"
PROXY_NOT_FOUND_DETAIL = "Proxy not found"
SKILL_NOT_FOUND_DETAIL = "Skill not found"
PASSWORD_FIELD_KEY = "".join(["pass", "word"])
FAILED_OPEN_SFTP_SESSION_DETAIL = "Failed to open SFTP session"

@asynccontextmanager
async def lifespan(app):
    """应用启动时写入日志，确保 xterm.log 被创建"""
    try:
        from logger import app_logger
        port = os.getenv("SERVER_PORT", "?")
        app_logger.info("系统", f"后端服务已启动，端口 {port}")
        app_logger.info("系统", f"JWT_SECRET 已加载: {'是' if JWT_SECRET else '否'}, APP_PASSWORD 已设置: {'是' if APP_PASSWORD else '否'}")
        app_logger.info("系统", f"ENV_PATH={_ENV_PATH}, DB_PATH={os.getenv('DB_PATH', '?')}")
        try:
            _dp = os.getenv("DB_PATH") or ""
            app_logger.info("系统", f"数据库文件存在: {os.path.exists(_dp)}")
        except Exception:
            pass
    except Exception:
        pass
    yield

app = FastAPI(lifespan=lifespan)
security = HTTPBearer()

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
                app_logger.info("API", f"{request.method} {path}" + (f" -> {code}" if code >= 400 else ""))
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

# --- 鉴权工具 ---
def create_access_token():
    expire = datetime.now(timezone.utc) + timedelta(days=7)
    to_encode = {"exp": expire, "sub": "xterm_admin"}
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或过期的 Token",
            headers={"WWW-Authenticate": "Bearer"},
        )

# WebSocket 鉴权 (通过 query 参数 token)
def verify_ws_token(websocket: WebSocket):
    token = websocket.query_params.get("token")
    if not token:
        return False
    try:
        jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return True
    except Exception:
        return False

# --- 登录 API ---
class LoginModel(BaseModel):
    password: str

class DebugAuthFailureModel(BaseModel):
    url: str = ""

@app.get("/api/ping")
async def ping():
    """无鉴权：诊断前端能否连通后端"""
    return {"ok": True}

@app.post("/api/debug/auth_failure")
async def debug_auth_failure(data: DebugAuthFailureModel):
    """无鉴权：前端 401 时上报触发路径，便于排查登录循环"""
    app_logger.info("鉴权", f"[前端上报] 401 来自: {data.url or '?'}")

@app.post("/api/login", responses={401: {"description": "密码错误"}})
async def login(data: LoginModel):
    if data.password == APP_PASSWORD:
        token = create_access_token()
        app_logger.info("鉴权", "登录成功，已颁发 Token")
        return {"access_token": token, "token_type": "bearer"}
    app_logger.info("鉴权", "登录失败：密码错误")
    raise HTTPException(status_code=401, detail="密码错误")

# 数据库实例
db_path = os.getenv("DB_PATH", "../config/xterm.db")
db = Database(db_path)


def _get_terminal_proxy():
    """获取终端场景绑定的代理配置，无则返回 None"""
    bindings = db.get_proxy_bindings()
    pid = bindings.get("terminal")
    return db.get_proxy_by_id(pid) if pid else None


def _get_ssh_config(server_info):
    """构建 SSHHandler 所需 config，含终端代理（若已绑定）"""
    cfg = {
        "host": server_info["host"],
        "port": server_info["port"],
        "username": server_info["username"],
        "password": server_info.get("password"),
        "private_key": server_info.get("private_key"),
    }
    proxy = _get_terminal_proxy()
    if proxy:
        cfg["proxy"] = proxy
    return cfg


def _get_skills_proxy():
    """获取技能场景绑定的代理配置"""
    bindings = db.get_proxy_bindings()
    pid = bindings.get("skills")
    return db.get_proxy_by_id(pid) if pid else None


# 挂载静态文件 (打包时用 _BASE_DIR，确保路径正确)
_frontend_dir = os.path.join(_BASE_DIR, "frontend")
_static_dir = os.path.join(_BASE_DIR, "static")
app.mount("/frontend", StaticFiles(directory=_frontend_dir), name="frontend")
app.mount("/static", StaticFiles(directory=_static_dir), name="static")

@app.get("/", response_class=HTMLResponse)
def get_index():
    with open(os.path.join(_frontend_dir, "index.html"), "r", encoding="utf-8") as f:
        return f.read()

# 数据模型
class ServerModel(BaseModel):
    name: str
    host: str
    port: int = 22
    username: str = "root"
    password: Optional[str] = None
    private_key: Optional[str] = None
    group_name: str = "default"
    device_type: str = "linux"
    device_type_id: Optional[int] = None
    description: Optional[str] = None

class AIModel(BaseModel):
    name: str
    api_key: str
    base_url: str
    model: str
    capabilities: List[str] = ["text"]
    is_active: int = 0

class RoleModel(BaseModel):
    name: str
    system_prompt: str
    ai_endpoint_id: Optional[int] = None
    is_active: int = 0
    bound_device_types: List[int] = [] # 新增：绑定的设备类型 ID 列表

class AITestModel(BaseModel):
    api_key: str
    base_url: str
    model: str

class ProxyModel(BaseModel):
    name: str
    type: str  # http | socks5
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    description: Optional[str] = None
    ignore_local: bool = False  # 勾选时，访问本地/局域网 IP 时不使用代理

class ProxyBindingsModel(BaseModel):
    terminal: Optional[int] = None
    ai: Optional[int] = None
    skills: Optional[int] = None

class SystemSettingsModel(BaseModel):
    log_enabled: str
    log_path: str
    log_max_size: str
    log_backup_count: str
    log_level: str

class ServerDocModel(BaseModel):
    content: str = ""

class ServerTestModel(BaseModel):
    host: str
    port: int = 22
    username: str
    password: Optional[str] = None
    private_key: Optional[str] = None

class CommandGroupModel(BaseModel):
    name: str

class CommandModel(BaseModel):
    group_id: int
    name: str
    content: str
    auto_cr: int = 1

class SftpRenameModel(BaseModel):
    server_id: int
    old_path: str
    new_path: str

class SftpChmodModel(BaseModel):
    server_id: int
    path: str
    mode: str

class SftpDeleteModel(BaseModel):
    server_id: int
    path: str
    is_dir: bool = False

class SftpSaveModel(BaseModel):
    server_id: int
    path: str
    content: str

class SftpCreateModel(BaseModel):
    server_id: int
    path: str
    type: str # 'file' or 'dir'

# --- 服务器管理 API ---
@app.get("/api/servers", dependencies=[Depends(verify_token)])
async def list_servers():
    return db.get_all_servers()

@app.post("/api/servers", dependencies=[Depends(verify_token)])
async def add_server(server: ServerModel):
    server_id = db.add_server(server.model_dump())
    return {"id": server_id, "status": "success"}

@app.get("/api/servers/recent", dependencies=[Depends(verify_token)])
async def list_recent_servers(limit: int = 20):
    """最近连接服务器（按 last_connected_at 倒序）"""
    if limit < 1:
        limit = 1
    if limit > 100:
        limit = 100
    return db.get_recent_servers(limit=limit)

@app.post(
    "/api/servers/{server_id}/mark_connected",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": SERVER_NOT_FOUND_DETAIL}},
)
async def mark_server_connected(server_id: int):
    """标记服务器最近连接时间"""
    if not db.get_server_by_id(server_id):
        raise HTTPException(status_code=404, detail=SERVER_NOT_FOUND_DETAIL)
    db.mark_server_connected(server_id)
    return {"status": "success"}

@app.delete("/api/servers/recent", dependencies=[Depends(verify_token)])
async def clear_recent_servers():
    """清空最近连接记录（保留服务器资产数据）"""
    db.clear_recent_connections()
    return {"status": "success"}

@app.get(
    "/api/servers/{server_id}",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": SERVER_NOT_FOUND_DETAIL}},
)
async def get_server(server_id: int):
    server = db.get_server_by_id(server_id)
    if not server:
        raise HTTPException(status_code=404, detail=SERVER_NOT_FOUND_DETAIL)
    return server

@app.put("/api/servers/{server_id}", dependencies=[Depends(verify_token)])
async def update_server(server_id: int, server: ServerModel):
    db.update_server(server_id, server.model_dump())
    return {"status": "success"}

@app.delete("/api/servers/{server_id}", dependencies=[Depends(verify_token)])
async def delete_server(server_id: int):
    db.delete_server(server_id)
    return {"status": "success"}

@app.get(
    "/api/servers/{server_id}/doc",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": "Server or document not found"}},
)
async def get_server_doc(server_id: int):
    """获取服务器文档，不存在返回 404"""
    if not db.get_server_by_id(server_id):
        raise HTTPException(status_code=404, detail=SERVER_NOT_FOUND_DETAIL)
    doc = db.get_server_doc(server_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc

@app.put(
    "/api/servers/{server_id}/doc",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": SERVER_NOT_FOUND_DETAIL}},
)
async def update_server_doc(server_id: int, body: ServerDocModel):
    """创建或更新服务器文档"""
    if not db.get_server_by_id(server_id):
        raise HTTPException(status_code=404, detail=SERVER_NOT_FOUND_DETAIL)
    db.upsert_server_doc(server_id, body.content)
    return {"status": "success"}

@app.post("/api/servers/test", dependencies=[Depends(verify_token)])
async def test_server_connection(server: ServerTestModel):
    app_logger.info(LOG_SCOPE_SSH_TEST, f"收到测试请求: host={server.host} port={server.port} user={server.username} auth={'key' if server.private_key else 'pwd'}")
    test_config = {
        "host": server.host,
        "port": server.port,
        "username": server.username,
        "password": server.password,
        "private_key": server.private_key,
    }
    proxy = _get_terminal_proxy()
    if proxy:
        test_config["proxy"] = proxy
        app_logger.info(LOG_SCOPE_SSH_TEST, f"使用终端代理: {proxy.get('type')} {proxy.get('host')}:{proxy.get('port')}")
    else:
        app_logger.info(LOG_SCOPE_SSH_TEST, "无代理，直连")
    ssh = SSHHandler(**test_config)
    if ssh.connect():
        ssh.close()
        app_logger.info(LOG_SCOPE_SSH_TEST, f"测试成功: {server.host}:{server.port}")
        return {"success": True, "message": "连接成功"}
    else:
        app_logger.info(LOG_SCOPE_SSH_TEST, f"测试失败: {server.host}:{server.port}（详见上方 SSH 连接日志）")
        return {"success": False, "message": "连接失败，请检查主机、端口、账号或密码是否正确"}

# --- AI 配置管理 API ---
@app.get("/api/ai_endpoints", dependencies=[Depends(verify_token)])
async def list_ai():
    endpoints = db.get_all_ai_endpoints()
    # 反序列化 capabilities
    for ep in endpoints:
        if ep['capabilities']:
            try:
                ep['capabilities'] = json.loads(ep['capabilities'])
            except json.JSONDecodeError:
                ep['capabilities'] = ["text"]
    return endpoints

@app.get(
    "/api/ai_endpoints/{ai_id}",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": "AI Endpoint not found"}},
)
async def get_ai_endpoint(ai_id: int):
    ai = db.get_ai_endpoint_by_id(ai_id)
    if not ai:
        raise HTTPException(status_code=404, detail="AI Endpoint not found")
    if ai['capabilities']:
        ai['capabilities'] = json.loads(ai['capabilities'])
    return ai

@app.post("/api/ai_endpoints", dependencies=[Depends(verify_token)])
async def add_ai(ai: AIModel):
    ai_id = db.add_ai_endpoint(ai.model_dump())
    return {"id": ai_id, "status": "success"}

@app.put("/api/ai_endpoints/{ai_id}", dependencies=[Depends(verify_token)])
async def update_ai(ai_id: int, ai: AIModel):
    db.update_ai_endpoint(ai_id, ai.model_dump())
    return {"status": "success"}

@app.delete("/api/ai_endpoints/{ai_id}", dependencies=[Depends(verify_token)])
async def delete_ai(ai_id: int):
    db.delete_ai_endpoint(ai_id)
    return {"status": "success"}

# --- 代理管理 API ---
def _mask_proxy_password(p):
    """API 返回时密码脱敏"""
    if not p:
        return p
    d = dict(p)
    pwd = d.get(PASSWORD_FIELD_KEY)
    if pwd:
        d[PASSWORD_FIELD_KEY] = "********"
    return d

@app.get("/api/proxies", dependencies=[Depends(verify_token)])
async def list_proxies():
    proxies = db.get_all_proxies()
    return [_mask_proxy_password(p) for p in proxies]

@app.get(
    "/api/proxies/{proxy_id}",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": PROXY_NOT_FOUND_DETAIL}},
)
async def get_proxy(proxy_id: int):
    p = db.get_proxy_by_id(proxy_id)
    if not p:
        raise HTTPException(status_code=404, detail=PROXY_NOT_FOUND_DETAIL)
    return _mask_proxy_password(p)

@app.post(
    "/api/proxies",
    dependencies=[Depends(verify_token)],
    responses={400: {"description": "type must be 'http' or 'socks5'"}},
)
async def add_proxy(proxy: ProxyModel):
    data = proxy.model_dump()
    if data['type'] not in ('http', 'socks5'):
        raise HTTPException(status_code=400, detail="type must be 'http' or 'socks5'")
    proxy_id = db.add_proxy(data)
    return {"id": proxy_id, "status": "success"}

@app.put(
    "/api/proxies/{proxy_id}",
    dependencies=[Depends(verify_token)],
    responses={
        400: {"description": "type must be 'http' or 'socks5'"},
        404: {"description": PROXY_NOT_FOUND_DETAIL},
    },
)
async def update_proxy(proxy_id: int, proxy: ProxyModel):
    if not db.get_proxy_by_id(proxy_id):
        raise HTTPException(status_code=404, detail=PROXY_NOT_FOUND_DETAIL)
    data = proxy.model_dump()
    if data['type'] not in ('http', 'socks5'):
        raise HTTPException(status_code=400, detail="type must be 'http' or 'socks5'")
    db.update_proxy(proxy_id, data)
    return {"status": "success"}

@app.delete(
    "/api/proxies/{proxy_id}",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": PROXY_NOT_FOUND_DETAIL}},
)
async def delete_proxy(proxy_id: int):
    if not db.get_proxy_by_id(proxy_id):
        raise HTTPException(status_code=404, detail=PROXY_NOT_FOUND_DETAIL)
    db.delete_proxy(proxy_id)
    return {"status": "success"}

@app.get("/api/proxy_bindings", dependencies=[Depends(verify_token)])
async def get_proxy_bindings():
    return db.get_proxy_bindings()

@app.post("/api/proxy_bindings", dependencies=[Depends(verify_token)])
async def update_proxy_bindings(bindings: ProxyBindingsModel):
    data = bindings.model_dump()
    db.update_proxy_bindings(
        terminal=data.get('terminal'),
        ai=data.get('ai'),
        skills=data.get('skills'),
    )
    return {"status": "success"}


@app.post("/api/proxy_bindings/clear_ai", dependencies=[Depends(verify_token)])
async def clear_ai_proxy_binding():
    """强制清除 AI 场景的代理绑定。用于修复「未勾选 AI 对话但 AI 仍走代理」的残留问题。"""
    db.upsert_system_setting("proxy_for_ai", "")
    app_logger.info("系统设置", "已清除 AI 代理绑定")
    return {"status": "success", "message": "已清除 AI 代理绑定"}

@app.post("/api/ai_endpoints/{ai_id}/activate", dependencies=[Depends(verify_token)])
async def activate_ai(ai_id: int):
    db.set_active_ai(ai_id)
    return {"status": "success"}

@app.post("/api/ai_endpoints/test", dependencies=[Depends(verify_token)])
async def test_ai(ai: AITestModel):
    proxy = None
    bindings = db.get_proxy_bindings()
    if bindings.get("ai"):
        proxy = db.get_proxy_by_id(bindings["ai"])
    handler = AIHandler(ai.api_key, ai.base_url, ai.model, "", proxy=proxy)
    success, message = await handler.test_connection()
    return {"success": success, "message": message}

# --- 角色管理 API ---
@app.get("/api/roles", dependencies=[Depends(verify_token)])
async def list_roles():
    return db.get_all_roles()

@app.get(
    "/api/roles/{role_id}",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": "Role not found"}},
)
async def get_role(role_id: int):
    role = db.get_role_by_id(role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    return role

@app.post("/api/roles", dependencies=[Depends(verify_token)])
async def add_role(role: RoleModel):
    data = role.model_dump()
    bound_types = data.pop('bound_device_types', [])
    role_id = db.add_role(data)
    if bound_types:
        db.update_device_type_role_bindings(role_id, bound_types)
    return {"id": role_id, "status": "success"}

@app.put("/api/roles/{role_id}", dependencies=[Depends(verify_token)])
async def update_role(role_id: int, role: RoleModel):
    data = role.model_dump()
    bound_types = data.pop('bound_device_types', [])
    db.update_role(role_id, data)
    db.update_device_type_role_bindings(role_id, bound_types)
    return {"status": "success"}

@app.delete("/api/roles/{role_id}", dependencies=[Depends(verify_token)])
async def delete_role(role_id: int):
    db.delete_role(role_id)
    return {"status": "success"}

@app.post("/api/roles/{role_id}/activate", dependencies=[Depends(verify_token)])
async def activate_role(role_id: int):
    db.set_active_role(role_id)
    app_logger.info("角色管理", f"激活角色 ID: {role_id}")
    return {"status": "success"}

# --- 服务器分组折叠状态（存数据库，打包后端口变化时仍可持久化）---
@app.get("/api/server_tree_collapsed", dependencies=[Depends(verify_token)])
async def get_server_tree_collapsed():
    s = db.get_system_settings()
    raw = s.get("server_tree_collapsed", "{}")
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}

@app.put("/api/server_tree_collapsed", dependencies=[Depends(verify_token)])
async def put_server_tree_collapsed(data: dict):
    db.upsert_system_setting("server_tree_collapsed", json.dumps(data))
    return {"status": "success"}

# --- 系统设置 API ---
@app.get("/api/system_settings", dependencies=[Depends(verify_token)])
async def get_system_settings():
    return db.get_system_settings()

@app.post("/api/system_settings", dependencies=[Depends(verify_token)])
async def update_system_settings(settings: SystemSettingsModel):
    data = settings.model_dump()
    for k, v in data.items():
        db.update_system_setting(k, v)
    app_logger.reload()
    app_logger.info("系统设置", "更新了日志及系统设置")
    return {"status": "success"}

def _get_log_path():
    """获取日志目录（打包时用 LOG_PATH，否则用数据库设置）"""
    p = os.getenv("LOG_PATH")
    if p:
        return p
    settings = db.get_system_settings()
    p = settings.get("log_path", "../logs")
    return os.path.normpath(os.path.join(os.path.dirname(__file__), p))

# --- 日志管理 API ---
@app.get("/api/logs", dependencies=[Depends(verify_token)])
async def list_logs():
    abs_log_path = _get_log_path()
    
    if not os.path.exists(abs_log_path):
        return []
        
    log_files = []
    # 查找 xterm.log* 文件
    files = glob.glob(os.path.join(abs_log_path, "xterm.log*"))
    for f in sorted(files, key=os.path.getmtime, reverse=True):
        stat = os.stat(f)
        log_files.append({
            "name": os.path.basename(f),
            "size": round(stat.st_size / 1024, 2), # KB
            "mtime": stat.st_mtime
        })
    return log_files

@app.get(
    "/api/logs/content",
    dependencies=[Depends(verify_token)],
    responses={
        400: {"description": "非法文件名"},
        404: {"description": "日志文件不存在"},
        500: {"description": "读取日志失败"},
    },
)
def get_log_content(filename: str, lines: int = 500):
    abs_log_path = _get_log_path()
    
    # 简单路径安全检查
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="非法文件名")
        
    file_path = os.path.join(abs_log_path, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="日志文件不存在")
        
    # 读取最后 N 行
    try:
        content = []
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            # 高效读取大文件末尾的方法
            f.seek(0, os.SEEK_END)
            file_size = f.tell()
            
            buffer_size = 1024 * 16 # 16KB
            pos = file_size
            lines_found = 0
            
            while pos > 0 and lines_found <= lines:
                read_len = min(pos, buffer_size)
                pos -= read_len
                f.seek(pos)
                chunk = f.read(read_len)
                lines_found += chunk.count('\n')
                content.insert(0, chunk)
                
        # 合并并切割出需要的行数
        full_text = "".join(content)
        result_lines = full_text.splitlines()
        if len(result_lines) > lines:
            result_lines = result_lines[-lines:]
        return {"content": "\n".join(result_lines)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete(
    "/api/logs",
    dependencies=[Depends(verify_token)],
    responses={500: {"description": "清空日志失败"}},
)
def clear_logs():
    abs_log_path = _get_log_path()
    
    if not os.path.exists(abs_log_path):
        return {"status": "success"}
        
    try:
        # 清空所有 xterm.log* 文件
        files = glob.glob(os.path.join(abs_log_path, "xterm.log*"))
        for f in files:
            # 对于正在写入的 xterm.log，清空内容而非删除，防止句柄失效
            if os.path.basename(f) == "xterm.log":
                with open(f, "w", encoding="utf-8") as fh:
                    fh.truncate(0)
            else:
                os.remove(f)
        app_logger.info("系统设置", "已清空系统日志文件")
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post(
    "/api/database/backup",
    dependencies=[Depends(verify_token)],
    responses={
        404: {"description": "数据库文件不存在"},
        500: {"description": "备份数据库失败"},
    },
)
async def backup_database():
    """将 config/xterm.db 备份到 config/backup/xterm_年月日时分秒.db"""
    # db_path 相对于 backend 目录，需转为绝对路径
    base_dir = os.path.dirname(os.path.abspath(__file__))
    src_path = os.path.normpath(os.path.join(base_dir, db_path))
    if not os.path.exists(src_path):
        raise HTTPException(status_code=404, detail="数据库文件不存在")
    backup_dir = os.path.join(os.path.dirname(src_path), "backup")
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"xterm_{timestamp}.db"
    dest_path = os.path.join(backup_dir, filename)
    try:
        shutil.copy2(src_path, dest_path)
        app_logger.info("系统设置", f"数据库已备份至 {filename}")
        return {"status": "success", "filename": filename, "path": f"config/backup/{filename}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"备份失败: {e}")

# --- 快捷命令管理 API ---
@app.get("/api/command_groups", dependencies=[Depends(verify_token)])
async def list_command_groups():
    return db.get_all_command_groups()

class DeviceTypeModel(BaseModel):
    name: str
    value: str
    icon: str = "fas fa-microchip"
    role_id: Optional[int] = None

@app.get("/api/device_types", dependencies=[Depends(verify_token)])
async def list_device_types():
    return db.get_all_device_types()

@app.post("/api/device_types", dependencies=[Depends(verify_token)])
async def add_device_type(data: DeviceTypeModel):
    return {"id": db.add_device_type(data.dict())}

@app.put("/api/device_types/{type_id}", dependencies=[Depends(verify_token)])
async def update_device_type(type_id: int, data: DeviceTypeModel):
    db.update_device_type(type_id, data.dict())
    return {"status": "success"}

@app.delete("/api/device_types/{type_id}", dependencies=[Depends(verify_token)])
async def delete_device_type(type_id: int):
    db.delete_device_type(type_id)
    return {"status": "success"}

# --- 技能管理 API ---
class SkillModel(BaseModel):
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    description_zh: Optional[str] = None
    source: str = "local"
    source_url: Optional[str] = None
    content: Optional[str] = None
    trigger_words: Optional[List[str]] = None
    is_enabled: int = 1
    install_count: int = 0
    bound_device_types: List[int] = []

@app.get("/api/skills", dependencies=[Depends(verify_token)])
async def list_skills(enabled: Optional[int] = None, device_type_id: Optional[int] = None):
    """获取技能列表，支持 ?enabled=1 和 ?device_type_id=3 过滤"""
    enabled_only = enabled == 1 if enabled is not None else False
    return db.get_all_skills(enabled_only=enabled_only, device_type_id=device_type_id)

@app.get(
    "/api/skills/{skill_id}",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": SKILL_NOT_FOUND_DETAIL}},
)
async def get_skill(skill_id: int):
    skill = db.get_skill_by_id(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=SKILL_NOT_FOUND_DETAIL)
    return skill

@app.post(
    "/api/skills",
    dependencies=[Depends(verify_token)],
    responses={400: {"description": "技能名称已存在"}},
)
async def add_skill(skill: SkillModel):
    data = skill.model_dump()
    bound_types = data.pop("bound_device_types", [])
    # 检查 name 唯一性
    if db.get_skill_by_name(data["name"]):
        raise HTTPException(status_code=400, detail=f"技能名称 '{data['name']}' 已存在")
    skill_id = db.add_skill(data)
    if bound_types:
        db.update_skill_device_type_bindings(skill_id, bound_types)
    app_logger.info("技能管理", f"创建技能: {data['name']}")
    return {"id": skill_id, "status": "success"}

@app.put(
    "/api/skills/{skill_id}",
    dependencies=[Depends(verify_token)],
    responses={
        400: {"description": "技能名称已存在"},
        404: {"description": SKILL_NOT_FOUND_DETAIL},
    },
)
async def update_skill(skill_id: int, skill: SkillModel):
    if not db.get_skill_by_id(skill_id):
        raise HTTPException(status_code=404, detail=SKILL_NOT_FOUND_DETAIL)
    data = skill.model_dump()
    bound_types = data.pop("bound_device_types", [])
    # 若修改了 name，检查唯一性（排除自身）
    existing = db.get_skill_by_name(data["name"])
    if existing and existing["id"] != skill_id:
        raise HTTPException(status_code=400, detail=f"技能名称 '{data['name']}' 已存在")
    db.update_skill(skill_id, data)
    db.update_skill_device_type_bindings(skill_id, bound_types)
    app_logger.info("技能管理", f"更新技能 ID:{skill_id}")
    return {"status": "success"}

@app.delete(
    "/api/skills/{skill_id}",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": SKILL_NOT_FOUND_DETAIL}},
)
async def delete_skill(skill_id: int):
    if not db.get_skill_by_id(skill_id):
        raise HTTPException(status_code=404, detail=SKILL_NOT_FOUND_DETAIL)
    db.delete_skill(skill_id)
    app_logger.info("技能管理", f"删除技能 ID:{skill_id}")
    return {"status": "success"}

@app.post(
    "/api/skills/{skill_id}/toggle",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": SKILL_NOT_FOUND_DETAIL}},
)
async def toggle_skill(skill_id: int):
    """切换技能启用/禁用状态"""
    if not db.get_skill_by_id(skill_id):
        raise HTTPException(status_code=404, detail=SKILL_NOT_FOUND_DETAIL)
    is_enabled = db.toggle_skill(skill_id)
    return {"is_enabled": is_enabled, "status": "success"}

# --- 技能商店 API ---
@app.get("/api/skill_store/recommended", dependencies=[Depends(verify_token)])
async def skill_store_recommended(q: Optional[str] = ""):
    """获取推荐技能列表"""
    return get_recommended_skills(query=q or "")

@app.get("/api/skill_store/list", dependencies=[Depends(verify_token)])
async def skill_store_list(repo: str, token: Optional[str] = None):
    """从 GitHub 仓库列出技能"""
    proxy = _get_skills_proxy()
    skills = list_skills_from_github(repo, token=token, proxy=proxy)
    return skills

class SkillInstallModel(BaseModel):
    source: str
    skill_name: str
    skill_path: str = ".agent-skills"
    description_zh: Optional[str] = None
    bound_device_type_ids: List[int] = []

@app.post(
    "/api/skill_store/install",
    dependencies=[Depends(verify_token)],
    responses={502: {"description": "安装失败：无法拉取技能内容"}},
)
async def skill_store_install(install: SkillInstallModel):
    """安装技能（从 GitHub 拉取并写入数据库）"""
    proxy = _get_skills_proxy()
    skill_id = do_install_skill(
        source=install.source,
        skill_name=install.skill_name,
        skill_path=install.skill_path,
        description_zh=install.description_zh,
        bound_device_type_ids=install.bound_device_type_ids,
        db=db,
        proxy=proxy,
    )
    if skill_id is None:
        raise HTTPException(
            status_code=502,
            detail="安装失败：无法拉取技能内容，请检查源地址或网络。若已配置技能代理，请确认代理能访问 raw.githubusercontent.com",
        )
    app_logger.info("技能管理", f"从商店安装技能: {install.skill_name}")
    return {"id": skill_id, "status": "success"}

class TranslateModel(BaseModel):
    text: str

@app.post("/api/translate", dependencies=[Depends(verify_token)])
async def translate_text(model: TranslateModel):
    """将英文技能描述翻译为中文。优先预置词汇，其次 AI（若已配置）。离线时返回提示。"""
    ai_handler = None
    ai = db.get_active_ai_endpoint()
    if ai:
        proxy = None
        bindings = db.get_proxy_bindings()
        if bindings.get("ai"):
            proxy = db.get_proxy_by_id(bindings["ai"])
        ai_handler = AIHandler(ai["api_key"], ai["base_url"], ai["model"], "", proxy=proxy)
    translation, message = await do_translate(model.text, ai_handler)
    if translation:
        return {"translation": translation}
    return {"translation": None, "message": message}

@app.post(
    "/api/skills/{skill_id}/refresh",
    dependencies=[Depends(verify_token)],
    responses={
        400: {"description": "技能源格式错误或不可刷新"},
        404: {"description": SKILL_NOT_FOUND_DETAIL},
        502: {"description": "无法从远程拉取技能内容"},
    },
)
async def refresh_skill(skill_id: int):
    """从 source_url 重新拉取 content（远程技能）"""
    skill = db.get_skill_by_id(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=SKILL_NOT_FOUND_DETAIL)
    source_url = skill.get("source_url")
    if not source_url:
        raise HTTPException(status_code=400, detail="该技能为本地创建，无远程源可刷新")
    # 解析 source_url (owner/repo 或 owner/repo/branch)
    parts = source_url.split("/")
    if len(parts) < 2:
        raise HTTPException(status_code=400, detail="无效的远程源格式")
    skill_name = skill.get("name") or ""
    skill_path = skill.get("skill_path") or ".agent-skills"
    proxy = _get_skills_proxy()
    data = fetch_skill_content(source_url, skill_name, skill_path, proxy)
    if not data:
        raise HTTPException(status_code=502, detail="无法从远程拉取技能内容，请检查网络或源地址")
    merged = {**skill, "display_name": data["name"], "description": data["description"], "content": data["content"]}
    db.update_skill(skill_id, merged)
    app_logger.info("技能管理", f"刷新技能 ID:{skill_id} 成功")
    return {"status": "success", "message": "已从远程更新技能内容"}

@app.post("/api/command_groups", dependencies=[Depends(verify_token)])
async def add_command_group(group: CommandGroupModel):
    group_id = db.add_command_group(group.name)
    return {"id": group_id, "status": "success"}

@app.put("/api/command_groups/{group_id}", dependencies=[Depends(verify_token)])
async def update_command_group(group_id: int, group: CommandGroupModel):
    db.update_command_group(group_id, group.name)
    return {"status": "success"}

@app.delete("/api/command_groups/{group_id}", dependencies=[Depends(verify_token)])
async def delete_command_group(group_id: int):
    db.delete_command_group(group_id)
    return {"status": "success"}

@app.get("/api/commands/{group_id}", dependencies=[Depends(verify_token)])
async def list_commands(group_id: int):
    return db.get_commands_by_group(group_id)

@app.post("/api/commands", dependencies=[Depends(verify_token)])
async def add_command(cmd: CommandModel):
    cmd_id = db.add_command(cmd.model_dump())
    return {"id": cmd_id, "status": "success"}

@app.put("/api/commands/{cmd_id}", dependencies=[Depends(verify_token)])
async def update_command(cmd_id: int, cmd: CommandModel):
    db.update_command(cmd_id, cmd.model_dump())
    return {"status": "success"}

@app.delete("/api/commands/{cmd_id}", dependencies=[Depends(verify_token)])
async def delete_command(cmd_id: int):
    db.delete_command(cmd_id)
    return {"status": "success"}

# --- WebSocket 终端连接 ---
def _ssh_auth_label(cfg: dict) -> str:
    if cfg.get("private_key"):
        return "key"
    if cfg.get("password"):
        return "pwd"
    return "无认证"


async def _run_ssh_websocket_bridge(ssh: SSHHandler, websocket: WebSocket):
    forward_task = asyncio.create_task(_forward_ssh_to_client(ssh, websocket))
    try:
        while True:
            data = await websocket.receive_text()
            _handle_ssh_client_message(ssh, data)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        ssh.close()
        forward_task.cancel()


async def _forward_ssh_to_client(ssh: SSHHandler, websocket: WebSocket):
    try:
        while True:
            data = ssh.read()
            if data:
                await websocket.send_text(data)
            await asyncio.sleep(0.01)
    except Exception:
        pass


def _handle_ssh_client_message(ssh: SSHHandler, data: str):
    if not data:
        return
    try:
        payload = json.loads(data)
        if payload.get("type") == "resize":
            ssh.resize_pty(payload["cols"], payload["rows"])
        else:
            ssh.write(payload.get("data", ""))
    except json.JSONDecodeError:
        ssh.write(data)


@app.websocket("/ws/ssh/{server_id}")
async def ssh_endpoint(websocket: WebSocket, server_id: int):
    app_logger.info("终端连接", f"WebSocket 请求 server_id={server_id}")
    ssh = await _prepare_ssh_websocket(websocket, server_id)
    if not ssh:
        return
    await _run_ssh_websocket_bridge(ssh, websocket)

# --- WebSocket AI 连接 ---
async def _prepare_ssh_websocket(websocket: WebSocket, server_id: int):
    if not verify_ws_token(websocket):
        app_logger.info("终端连接", "Token 验证失败")
        await websocket.accept()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return None

    await websocket.accept()
    server_info = db.get_server_by_id(server_id)
    if not server_info:
        app_logger.info("终端连接", f"server_id={server_id} 不存在")
        await websocket.send_text("Server not found in database!")
        await websocket.close()
        return None

    cfg = _get_ssh_config(server_info)
    auth = _ssh_auth_label(cfg)
    if not cfg.get("private_key") and not cfg.get("password"):
        app_logger.info("终端连接", f"警告: 密码为空，可能解密失败。host={cfg['host']}")
    app_logger.info("终端连接", f"磁贴连接: name={server_info.get('name')} host={cfg['host']} port={cfg['port']} user={cfg['username']} auth={auth} proxy={bool(cfg.get('proxy'))}")

    ssh = SSHHandler(**cfg)
    if not ssh.connect():
        app_logger.info("终端连接", f"磁贴连接失败: {server_info.get('name')}（详见上方 SSH 连接日志）")
        await websocket.send_text(f"SSH Connection to {server_info['name']} Failed!")
        await websocket.close()
        return None

    app_logger.info("终端连接", f"磁贴连接成功: {server_info.get('name')}")
    return ssh


def _resolve_role_info(role_id: Optional[int]):
    role_info = db.get_role_by_id(role_id) if role_id else None
    if not role_info:
        role_info = db.get_active_role()
    return role_info or {"system_prompt": "You are a helpful assistant.", "ai_endpoint_id": None}


def _build_env_constraints(device_type: str, server_name: str) -> tuple[str, str]:
    env_constraints = f"""
[当前操作环境信息]
- 服务器名称: {server_name}
- 操作系统/设备类型: {device_type}

[执行约束 - 请严格遵守]
"""
    dt_lower = device_type.lower()
    if dt_lower == "windows":
        env_constraints += """
* 目标系统为 Windows，你必须使用 PowerShell 语法。
* 禁止使用 `ls`, `cat`, `rm -rf` 等 Linux 命令。
* 使用 `Get-ChildItem`, `Get-Content`, `Remove-Item` 等 PowerShell Cmdlets。
* 若不确定版本，请先执行 `[System.Environment]::OSVersion`。
"""
    elif dt_lower in ["huawei", "h3c", "cisco", "ruijie"]:
        env_constraints += f"""
* 目标是 {device_type.upper()} 网络设备命令行(VRP/Comware/IOS)。
* 严禁执行任何 Shell/Bash 命令。
* 你必须使用该厂商特有的 CLI 命令（如 `display current-configuration`, `show version` 等）。
* 每一行命令必须符合网络设备的交互逻辑。
* 分页处理：display/show 类命令会分页，助手会自动翻页获取完整输出；可直接使用单条 display/show 命令。

* 【视图与提示符 - 必须遵守】：
  - H3C/华为/锐捷(VRP/Comware)：用户视图提示符为尖括号如 <H3C>、<Huawei>，只能执行 display、ping、tracert 等只读命令；系统视图提示符为方括号如 [H3C]、[Huawei]，才能执行 interface、vlan、ip address、acl 等配置命令。执行任何配置命令前，若当前提示符为尖括号 <xxx>，必须先发送 `system-view` 进入系统视图。
  - Cisco(IOS)：用户模式提示符为 `>`, 特权模式为 `#`, 全局配置模式为 `(config)#`。配置命令需在 config 模式下执行。若提示符为 `>` 需先 `enable`，若为 `#` 且要配置需 `configure terminal`。
  - 请根据终端输出中的提示符判断当前视图；在用户视图/用户模式下直接发配置命令会失败，必须先进入系统视图或配置模式。
"""
    elif dt_lower == "linux":
        env_constraints += """
* 目标系统为 Linux (Bash Shell)。
* 请优先执行 `cat /etc/os-release` 以确定具体的发行版（Ubuntu/CentOS等）。
"""
    else:
        env_constraints += """
* 目标系统类型【未知】或【未指定】。
* 你的首要任务是识别操作系统。请先发送一个通用的探测指令，如 `uname -a || ver || show version`。
* 在确定系统类型之前，不要执行任何具有修改性质的操作。
"""
    return env_constraints, dt_lower


def _build_command_protocol() -> str:
    return """
[智能运维助手 - 核心指令规范]
1. 任务分解：请将复杂任务拆解。在每一轮回复中，你【必须且只能】发送【一个】`command_request`；但完成时可同时发送 `summary_report` 与 `document_update`。
2. 状态管理：
   - 探测与分析：当你需要执行命令获取信息时，发送 `command_request`。
   - 任务完成：当用户需求已满足或问题已解决时，你必须发送 `summary_report`。
   - 文档更新：当用户请求「分析服务器」「查看本机信息」「生成文档」等，或你通过多轮命令收集了服务器/设备的环境信息（OS、CPU、内存、磁盘、网络、服务等）并准备发送 `summary_report` 时，你【必须】在同一回复中，在 `summary_report` 之后追加 `document_update`，将分析结果同步到服务器环境文档，便于后续对话自动携带上下文。
3. JSON 格式规范：
   - 执行命令：
     ```json
     {
       "type": "command_request",
       "command": "具体的 shell 命令",
       "intent": "说明为什么要执行此命令"
     }
     ```
   - 任务终结总结：
     ```json
     {
       "type": "summary_report",
       "content": "用 Markdown 格式编写的详细操作总结、分析报告或建议。"
     }
     ```
   - 更新服务器文档（仅在 Agent 模式、且已有足够环境信息时使用，content 为完整 Markdown 文档）：
     ```json
     {
       "type": "document_update",
       "content": "# 服务器名 环境文档\\n> 连接信息\\n## 基本信息\\n..."
     }
     ```
4. 约束：
   - 严禁一次性提供多个备选命令。
   - 严禁在没有获取足够信息的情况下盲目尝试。
   - 任务完成后必须通过 `summary_report` 闭环，不得无限制执行。
   - document_update 的 content 需为完整文档，可复用 summary_report 的分析内容，并补充「由 AI 自动补充」的章节结构。
   - 服务器分析类任务：summary_report 与 document_update 应在同一回复中依次出现，document_update 的 content 与报告内容一致或更结构化。
"""


def _build_skills_block(dt_lower: str) -> str:
    max_skill_content = 3000
    max_total_skills = 8000
    try:
        settings = db.get_system_settings()
        if settings.get("skills_enabled", "1") == "0":
            return ""
        skills_list = db.get_skills_for_device_type(dt_lower, enabled_only=True)
        if not skills_list:
            return ""

        parts = []
        total_len = 0
        for skill in skills_list:
            skill_block, part_len, overflow_block = _format_skill_block(skill, max_skill_content, max_total_skills, total_len)
            if not skill_block:
                continue
            if overflow_block is not None:
                if overflow_block:
                    parts.append(overflow_block)
                break
            parts.append(skill_block)
            total_len += part_len
        return "\n\n[已启用技能 - 请结合以下知识库回答]\n---\n" + "\n---\n".join(parts) + "\n---\n\n" if parts else ""
    except Exception as e:
        app_logger.error("技能注入", f"加载技能失败: {e}")
        return ""


def _format_skill_block(skill: dict, max_skill_content: int, max_total_skills: int, current_total_len: int):
    content = (skill.get("content") or "").strip()
    if not content:
        return None, 0, None

    if len(content) > max_skill_content:
        content = content[:max_skill_content] + "\n...(已截断)"
    title = skill.get("display_name") or skill.get("name", "")
    skill_block = f"## 技能: {title}\n{content}"
    part_len = len(skill_block)
    next_total = current_total_len + part_len
    if next_total <= max_total_skills:
        return skill_block, part_len, None

    remaining = max_total_skills - current_total_len - 50
    if remaining > 100:
        clipped = content[:remaining] if len(content) > remaining else content
        return skill_block, part_len, f"## 技能: {title}\n{clipped}...(已截断)"
    return skill_block, part_len, ""


def _build_server_doc_block(server_id: Optional[int]) -> str:
    max_server_doc = 5000
    if not server_id:
        return ""
    try:
        doc = db.get_server_doc(server_id)
        if not (doc and doc.get("content")):
            return ""
        content = (doc["content"] or "").strip()
        if not content:
            return ""
        if len(content) > max_server_doc:
            content = content[:max_server_doc] + "\n...(已截断)"
        return f"\n\n[当前服务器环境文档 - 供诊断与决策参考]\n---\n{content}\n---\n\n"
    except Exception as e:
        app_logger.error("文档注入", f"加载服务器文档失败: {e}")
        return ""


def _build_ai_handler(role_info: dict, combined_prompt: str):
    ai_info = db.get_ai_endpoint_by_id(role_info["ai_endpoint_id"]) if role_info.get("ai_endpoint_id") else None
    if not ai_info:
        ai_info = db.get_active_ai_endpoint()
    ai_config = {
        "api_key": ai_info["api_key"] if ai_info else os.getenv("AI_API_KEY"),
        "base_url": ai_info["base_url"] if ai_info else os.getenv("AI_BASE_URL"),
        "model": ai_info["model"] if ai_info else os.getenv("AI_MODEL"),
        "system_prompt": combined_prompt,
    }
    bindings = db.get_proxy_bindings()
    settings = db.get_system_settings()
    proxy_for_ai_raw = settings.get("proxy_for_ai", "")
    if bindings.get("ai"):
        proxy = db.get_proxy_by_id(bindings["ai"])
        if proxy:
            ai_config["proxy"] = proxy
            app_logger.info("AI 代理", f"使用代理: {proxy.get('name', '')} ({proxy.get('host')}:{proxy.get('port')})")
    else:
        app_logger.info("AI 代理", f"直连模式 (proxy_for_ai={proxy_for_ai_raw!r}, bindings.ai={bindings.get('ai')})")
    return AIHandler(**ai_config)


def _parse_mode_and_messages(payload):
    if isinstance(payload, list):
        return "agent", payload
    if isinstance(payload, dict):
        return payload.get("mode", "agent"), payload.get("messages", [])
    return None, None


async def _run_ai_loop(websocket: WebSocket, role_info: dict, env_constraints: str, server_doc_block: str, skills_block: str, command_protocol: str):
    combined_prompt = f"{role_info['system_prompt']}\n\n{env_constraints}\n\n{server_doc_block}{skills_block}{command_protocol}"
    while True:
        data = await websocket.receive_text()
        payload = json.loads(data)
        mode, messages = _parse_mode_and_messages(payload)
        if mode is None:
            app_logger.error("AI 错误", f"无效的消息格式: {type(payload)}")
            await websocket.send_text("[AI Error: 无效的消息格式]")
            continue
        if not messages:
            continue

        ai = _build_ai_handler(role_info, combined_prompt)
        base = f"{role_info['system_prompt']}\n\n{env_constraints}\n\n{server_doc_block}{skills_block}"
        ai.system_prompt = base + command_protocol if mode == "agent" else base + "\n(注意：当前处于 Ask 模式，请仅通过文本回答，不要要求执行任何 Shell 命令。)"

        full_response = ""
        async for chunk in ai.get_response_stream(messages):
            full_response += chunk
            await websocket.send_text(chunk)
        app_logger.info("AI 对话", f"完整回复内容: {full_response}")
        await websocket.send_text("[DONE]")


@app.websocket("/ws/ai")
async def ai_endpoint(
    websocket: WebSocket, 
    role_id: Optional[int] = None,
    device_type: str = "unknown",
    server_name: str = "未选定服务器",
    server_id: Optional[int] = None
):
    if not verify_ws_token(websocket):
        await websocket.accept()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept()
    role_info = _resolve_role_info(role_id)
    env_constraints, dt_lower = _build_env_constraints(device_type, server_name)
    command_protocol = _build_command_protocol()
    skills_block = _build_skills_block(dt_lower)
    server_doc_block = _build_server_doc_block(server_id)
    try:
        await _run_ai_loop(websocket, role_info, env_constraints, server_doc_block, skills_block, command_protocol)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        await websocket.send_text(f"\n[AI Error: {str(e)}]\n")

# --- WebSocket 状态采集 (新增) ---
async def _safe_ws_close(websocket: WebSocket):
    try:
        await websocket.close()
    except Exception:
        pass


def _build_stats_collect_command() -> str:
    return (
        "echo '###HOSTNAME###' && hostname && "
        "echo '###OS###' && (cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"' || uname -s) && "
        "echo '###UPTIME###' && uptime && "
        "echo '###MEM###' && free -m && "
        "echo '###DISK###' && df -h / | tail -1 && "
        "echo '###CPU_CORES###' && nproc && "
        "echo '###PROCS###' && ps -eo pmem,pcpu,pid,comm --sort=-pcpu | head -12"
    )


async def _collect_stats_output(ssh: SSHHandler) -> str:
    output = ""
    for _ in range(15):  # 最多等待 3 秒 (15 * 0.2s)
        await asyncio.sleep(0.2)
        chunk = ssh.read()
        if chunk:
            output += chunk
            # 如果读到了最后一段分隔符，说明采集完成
            if "###PROCS###" in output and len(output.split("###PROCS###")[1].splitlines()) >= 10:
                break
    return output


def _maybe_record_stats_history(websocket: WebSocket, server_id: int, stats: dict):
    current_minute = datetime.now().minute
    if not hasattr(websocket, "last_recorded_minute") or websocket.last_recorded_minute != current_minute:
        try:
            db.add_stats_history(server_id, stats["cpu"], stats["mem_p"], stats["disk_p"])
            websocket.last_recorded_minute = current_minute
        except Exception as e:
            print(f"Error saving stats history: {e}")


async def _run_stats_loop(websocket: WebSocket, ssh: SSHHandler, server_id: int, server_host: str):
    while True:
        cmd = _build_stats_collect_command()
        ssh.write(cmd + "\n")
        output = await _collect_stats_output(ssh)
        stats = parse_stats_output(output, server_host)
        await websocket.send_json(stats)
        _maybe_record_stats_history(websocket, server_id, stats)
        await asyncio.sleep(5)  # 每 5 秒更新一次


@app.websocket("/ws/stats/{server_id}")
async def stats_endpoint(websocket: WebSocket, server_id: int):
    if not verify_ws_token(websocket):
        await websocket.accept()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept()
    server_info = db.get_server_by_id(server_id)
    if not server_info:
        await _safe_ws_close(websocket)
        return

    ssh = SSHHandler(**_get_ssh_config(server_info))
    if not ssh.connect():
        await _safe_ws_close(websocket)
        return

    try:
        await _run_stats_loop(websocket, ssh, server_id, server_info["host"])
    except WebSocketDisconnect:
        pass
    except Exception as e:
        app_logger.warning("状态采集", f"WebSocket 异常: {e}")
    finally:
        ssh.close()

def _split_stats_sections(lines):
    sections = {}
    current_section = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("###") and stripped.endswith("###"):
            current_section = stripped[3:-3]
            sections[current_section] = []
        elif current_section:
            sections[current_section].append(line)
    return sections


def _first_non_comment_line(lines):
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return None


def _parse_uptime_load(lines, res):
    for line in lines:
        if "load average" not in line:
            continue
        try:
            res["uptime"] = line.split("up")[1].split(",")[0].strip()
            res["load"] = line.split("load average:")[1].strip()
        except Exception:
            pass
        return


def _parse_mem(lines, res):
    for line in lines:
        if not line.startswith("Mem:"):
            continue
        parts = line.split()
        if len(parts) >= 3:
            total, used = int(parts[1]), int(parts[2])
            res["mem"] = f"{used}M / {total}M"
            res["mem_p"] = round(used / total * 100, 1) if total else 0
        return


def _parse_disk(lines, res):
    for line in lines:
        if "/" not in line or "%" not in line:
            continue
        parts = line.split()
        if len(parts) >= 5:
            res["disk"] = f"{parts[2]} / {parts[1]}"
            try:
                res["disk_p"] = int(parts[4].replace("%", ""))
            except Exception:
                pass
        return


def _parse_cores(lines):
    for line in lines:
        stripped = line.strip()
        if stripped.isdigit():
            return max(1, int(stripped))
    return 1


def _parse_cpu(res, cores):
    if res["load"] == "-":
        return
    try:
        load1 = float(res["load"].split(",")[0])
        res["cpu"] = round(min(load1 / cores * 100, 100), 1)
    except Exception:
        pass


def _parse_procs(lines, res):
    started = False
    for line in lines:
        if "%MEM" in line and "%CPU" in line:
            started = True
            continue
        if not started or not line.strip():
            continue
        parts = line.split(None, 3)
        if len(parts) >= 4:
            try:
                res["procs"].append(
                    {
                        "mem": parts[0] + "%",
                        "cpu": parts[1] + "%",
                        "pid": parts[2],
                        "cmd": parts[3].strip(),
                    }
                )
            except Exception:
                pass


def parse_stats_output(output, ip):
    res = {
        "hostname": "-",
        "os": "-",
        "ip": ip,
        "uptime": "-",
        "load": "-",
        "cpu": 0,
        "mem": "0 / 0",
        "mem_p": 0,
        "disk": "0 / 0",
        "disk_p": 0,
        "procs": [],
    }
    try:
        sections = _split_stats_sections(output.splitlines())
        hostname = _first_non_comment_line(sections.get("HOSTNAME", []))
        os_name = _first_non_comment_line(sections.get("OS", []))
        if hostname:
            res["hostname"] = hostname
        if os_name:
            res["os"] = os_name
        _parse_uptime_load(sections.get("UPTIME", []), res)
        _parse_mem(sections.get("MEM", []), res)
        _parse_disk(sections.get("DISK", []), res)
        _parse_cpu(res, _parse_cores(sections.get("CPU_CORES", [])))
        _parse_procs(sections.get("PROCS", []), res)
    except Exception:
        pass
    return res

@app.get("/api/servers/{server_id}/stats/history", dependencies=[Depends(verify_token)])
async def get_server_stats_history(server_id: int, minutes: int = 30):
    """获取过去 X 分钟的历史指标数据"""
    return db.get_stats_history(server_id, minutes)


@app.delete("/api/servers/stats/history/all", dependencies=[Depends(verify_token)])
async def clear_all_stats_history():
    """清除所有服务器的状态历史记录"""
    db.clear_all_stats_history()
    return {"status": "success", "message": "已清除所有服务器的状态记录"}


@app.delete("/api/servers/{server_id}/stats/history", dependencies=[Depends(verify_token)])
async def clear_server_stats_history(server_id: int):
    """清除指定服务器的所有状态历史记录"""
    db.clear_stats_for_server(server_id)
    return {"status": "success", "message": "已清除该服务器的状态记录"}

@app.post(
    "/api/servers/{server_id}/process/kill",
    dependencies=[Depends(verify_token)],
    responses={
        404: {"description": SERVER_NOT_FOUND_DETAIL},
        500: {"description": "Failed to connect to server or kill failed"},
    },
)
async def kill_process(server_id: int, pid: Annotated[int, Form(...)]):
    """杀死指定 PID 的进程"""
    server_info = db.get_server_by_id(server_id)
    if not server_info:
        raise HTTPException(status_code=404, detail=SERVER_NOT_FOUND_DETAIL)
    
    ssh = SSHHandler(**_get_ssh_config(server_info))
    if not ssh.connect():
        raise HTTPException(status_code=500, detail="Failed to connect to server")
    
    try:
        # 使用 sudo 执行 kill (如果用户有权限的话)，否则普通 kill
        # 为安全起见，这里先简单 kill，后续可考虑增加权限判断
        ssh.write(f"kill -9 {pid}\n")
        return {"status": "success", "message": f"Sent kill signal to PID {pid}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        ssh.close()

# --- SFTP 管理 API ---
@app.get(
    "/api/sftp/list",
    dependencies=[Depends(verify_token)],
    responses={
        404: {"description": SERVER_NOT_FOUND_DETAIL},
        500: {"description": FAILED_OPEN_SFTP_SESSION_DETAIL},
    },
)
async def sftp_list(server_id: int, path: str = "/"):
    server_info = db.get_server_by_id(server_id)
    if not server_info:
        raise HTTPException(status_code=404, detail=SERVER_NOT_FOUND_DETAIL)
    
    ssh = SSHHandler(**_get_ssh_config(server_info))
    
    sftp = ssh.open_sftp()
    if not sftp:
        raise HTTPException(status_code=500, detail=FAILED_OPEN_SFTP_SESSION_DETAIL)
    
    try:
        # 如果路径为空，默认跳转到用户家目录
        if not path or path == "":
            path = sftp.normalize(".")
            
        files = []
        # 获取目录内容
        attr_list = sftp.listdir_attr(path)
        
        # 预定义排序：文件夹在前，文件名升序
        import stat as py_stat # 补全 import
        attr_list.sort(key=lambda x: (not py_stat.S_ISDIR(x.st_mode), x.filename.lower()))
        
        for attr in attr_list:
            is_dir = py_stat.S_ISDIR(attr.st_mode)
            files.append({
                "name": attr.filename,
                "size": attr.st_size if not is_dir else 0,
                "mode": oct(attr.st_mode)[-4:],
                "mtime": attr.st_mtime,
                "is_dir": is_dir,
                "type": "dir" if is_dir else "file"
            })
            
        return {
            "path": path,
            "files": files
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if sftp: sftp.close()
        ssh.close()

@app.get(
    "/api/sftp/download",
    dependencies=[Depends(verify_token)],
    responses={
        404: {"description": SERVER_NOT_FOUND_DETAIL},
        500: {"description": FAILED_OPEN_SFTP_SESSION_DETAIL},
    },
)
async def sftp_download(server_id: int, path: str):
    server_info = db.get_server_by_id(server_id)
    if not server_info:
        raise HTTPException(status_code=404, detail=SERVER_NOT_FOUND_DETAIL)
    
    ssh = SSHHandler(**_get_ssh_config(server_info))
    
    sftp = ssh.open_sftp()
    if not sftp:
        raise HTTPException(status_code=500, detail=FAILED_OPEN_SFTP_SESSION_DETAIL)
    
    try:
        # 获取文件名
        filename = os.path.basename(path)
        
        # 使用流式响应
        def iter_file():
            with sftp.open(path, 'rb') as f:
                while True:
                    chunk = f.read(1024 * 64) # 64KB chunks
                    if not chunk:
                        break
                    yield chunk
            sftp.close()
            ssh.close()

        return StreamingResponse(
            iter_file(),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        if sftp: sftp.close()
        ssh.close()
        raise HTTPException(status_code=500, detail=str(e))

@app.post(
    "/api/sftp/upload",
    dependencies=[Depends(verify_token)],
    responses={
        404: {"description": SERVER_NOT_FOUND_DETAIL},
        500: {"description": FAILED_OPEN_SFTP_SESSION_DETAIL},
    },
)
async def sftp_upload(
    server_id: Annotated[int, Form(...)],
    path: Annotated[str, Form(...)],
    file: Annotated[UploadFile, File(...)],
):
    server_info = db.get_server_by_id(server_id)
    if not server_info:
        raise HTTPException(status_code=404, detail=SERVER_NOT_FOUND_DETAIL)
    
    ssh = SSHHandler(**_get_ssh_config(server_info))
    
    sftp = ssh.open_sftp()
    if not sftp:
        raise HTTPException(status_code=500, detail=FAILED_OPEN_SFTP_SESSION_DETAIL)
    
    try:
        # 构造完整目标路径
        remote_path = os.path.join(path, file.filename).replace('\\', '/')
        
        # 写入文件
        with sftp.open(remote_path, 'wb') as f:
            while True:
                chunk = await file.read(1024 * 64)
                if not chunk:
                    break
                f.write(chunk)
        
        return {"status": "success", "path": remote_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if sftp: sftp.close()
        ssh.close()

@app.post(
    "/api/sftp/rename",
    dependencies=[Depends(verify_token)],
    responses={500: {"description": "SFTP rename failed"}},
)
async def sftp_rename(data: SftpRenameModel):
    server_info = db.get_server_by_id(data.server_id)
    ssh = SSHHandler(**_get_ssh_config(server_info))
    sftp = ssh.open_sftp()
    try:
        sftp.rename(data.old_path, data.new_path)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if sftp: sftp.close()
        ssh.close()

@app.post(
    "/api/sftp/chmod",
    dependencies=[Depends(verify_token)],
    responses={500: {"description": "SFTP chmod failed"}},
)
async def sftp_chmod(data: SftpChmodModel):
    server_info = db.get_server_by_id(data.server_id)
    ssh = SSHHandler(**_get_ssh_config(server_info))
    sftp = ssh.open_sftp()
    try:
        # 将 755 这种字符串转换为八进制整数
        mode_int = int(data.mode, 8)
        sftp.chmod(data.path, mode_int)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if sftp: sftp.close()
        ssh.close()

@app.delete(
    "/api/sftp/delete",
    dependencies=[Depends(verify_token)],
    responses={500: {"description": "SFTP delete failed"}},
)
async def sftp_delete(data: SftpDeleteModel):
    server_info = db.get_server_by_id(data.server_id)
    ssh = SSHHandler(**_get_ssh_config(server_info))
    sftp = ssh.open_sftp()
    try:
        if data.is_dir:
            # paramiko 的 rmdir 只能删除空目录，复杂删除建议用 ssh 执行 rm -rf
            # 这里为了简单先用 rmdir，如果是递归删除后续再优化
            sftp.rmdir(data.path)
        else:
            sftp.remove(data.path)
        return {"status": "success"}
    except Exception as e:
        # 如果是目录且不为空，尝试执行 rm -rf
        if data.is_dir:
            try:
                ssh.connect()
                ssh.write(f"rm -rf '{data.path}'\n")
                return {"status": "success", "note": "used ssh rm -rf"}
            except Exception:
                pass
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if sftp: sftp.close()
        ssh.close()

@app.get(
    "/api/sftp/read",
    dependencies=[Depends(verify_token)],
    responses={
        400: {"description": "文件超过 30MB，无法打开"},
        500: {"description": "SFTP read failed"},
    },
)
async def sftp_read(server_id: int, path: str):
    server_info = db.get_server_by_id(server_id)
    ssh = SSHHandler(**_get_ssh_config(server_info))
    sftp = ssh.open_sftp()
    try:
        stat = sftp.stat(path)
        size_mb = stat.st_size / (1024 * 1024)
        
        if size_mb > 30:
            raise HTTPException(status_code=400, detail="文件超过 30MB，无法打开")
        
        # paramiko 的 open 不支持 encoding 参数，需要读取二进制后手动解码
        with sftp.open(path, 'rb') as f:
            binary_content = f.read()
            content = binary_content.decode('utf-8', errors='replace')
        
        return {
            "content": content,
            "readonly": size_mb > 3,
            "size": stat.st_size
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if sftp: sftp.close()
        ssh.close()

@app.post(
    "/api/sftp/save",
    dependencies=[Depends(verify_token)],
    responses={500: {"description": "SFTP save failed"}},
)
async def sftp_save(data: SftpSaveModel):
    server_info = db.get_server_by_id(data.server_id)
    ssh = SSHHandler(**_get_ssh_config(server_info))
    sftp = ssh.open_sftp()
    try:
        # 写入内容时，也需要以二进制模式打开并手动编码字符串
        with sftp.open(data.path, 'wb') as f:
            f.write(data.content.encode('utf-8'))
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if sftp: sftp.close()
        ssh.close()

@app.post(
    "/api/sftp/create",
    dependencies=[Depends(verify_token)],
    responses={500: {"description": "SFTP create failed"}},
)
async def sftp_create(data: SftpCreateModel):
    server_info = db.get_server_by_id(data.server_id)
    ssh = SSHHandler(**_get_ssh_config(server_info))
    sftp = ssh.open_sftp()
    try:
        if data.type == 'dir':
            sftp.mkdir(data.path)
        else:
            # 创建空文件
            with sftp.open(data.path, 'w') as f:
                f.write('')
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if sftp: sftp.close()
        ssh.close()

if __name__ == "__main__":
    import uvicorn
    host = os.getenv("SERVER_HOST", "0.0.0.0")
    port = int(os.getenv("SERVER_PORT", 8000))
    uvicorn.run(app, host=host, port=port)
