from fastapi import Depends, HTTPException, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from typing import List, Optional
import os
import json
import jwt
from datetime import datetime, timedelta, timezone

from database import Database


JWT_SECRET = os.getenv("JWT_SECRET", "xterm_secret_key_999")
JWT_ALGORITHM = "HS256"
APP_PASSWORD = os.getenv("APP_PASSWORD", "admin")
SERVER_NOT_FOUND_DETAIL = "Server not found"
LOG_SCOPE_SSH_TEST = "SSH测试"
PROXY_NOT_FOUND_DETAIL = "Proxy not found"
SKILL_NOT_FOUND_DETAIL = "Skill not found"
PASSWORD_FIELD_KEY = "".join(["pass", "word"])
FAILED_OPEN_SFTP_SESSION_DETAIL = "Failed to open SFTP session"

_BASE_DIR = os.getenv("BUNDLE_PATH") or os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
)
FRONTEND_DIR = os.path.join(_BASE_DIR, "frontend")
STATIC_DIR = os.path.join(_BASE_DIR, "static")

security = HTTPBearer()

db_path = os.getenv("DB_PATH", "../config/xterm.db")
db = Database(db_path)


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


def verify_ws_token(websocket: WebSocket):
    token = websocket.query_params.get("token")
    if not token:
        return False
    try:
        jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return True
    except Exception:
        return False


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


def get_log_path():
    """获取日志目录（打包时用 LOG_PATH，否则用数据库设置）"""
    p = os.getenv("LOG_PATH")
    if p:
        return p
    settings = db.get_system_settings()
    p = settings.get("log_path", "../logs")
    return os.path.normpath(os.path.join(os.path.dirname(__file__), p))


class LoginModel(BaseModel):
    password: str


class DebugAuthFailureModel(BaseModel):
    url: str = ""


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
    bound_device_types: List[int] = []


class AITestModel(BaseModel):
    api_key: str
    base_url: str
    model: str


class ProxyModel(BaseModel):
    name: str
    type: str
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    description: Optional[str] = None
    ignore_local: bool = False


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
    type: str


class DeviceTypeModel(BaseModel):
    name: str
    value: str
    icon: str = "fas fa-microchip"
    role_id: Optional[int] = None


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


class SkillInstallModel(BaseModel):
    source: str
    skill_name: str
    skill_path: str = ".agent-skills"
    description_zh: Optional[str] = None
    bound_device_type_ids: List[int] = []


class TranslateModel(BaseModel):
    text: str
