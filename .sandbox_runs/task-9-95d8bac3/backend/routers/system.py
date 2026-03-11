from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from datetime import datetime
import glob
import json
import os
import shutil

from core import (
    CommandGroupModel,
    CommandModel,
    DeviceTypeModel,
    FRONTEND_DIR,
    PASSWORD_FIELD_KEY,
    PROXY_NOT_FOUND_DETAIL,
    ProxyBindingsModel,
    ProxyModel,
    SystemSettingsModel,
    db,
    db_path,
    get_log_path,
    verify_token,
)
from logger import app_logger


router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def get_index():
    with open(os.path.join(FRONTEND_DIR, "index.html"), "r", encoding="utf-8") as f:
        return f.read()


def _mask_proxy_password(p):
    """API 返回时密码脱敏"""
    if not p:
        return p
    d = dict(p)
    pwd = d.get(PASSWORD_FIELD_KEY)
    if pwd:
        d[PASSWORD_FIELD_KEY] = "********"
    return d


@router.get("/api/proxies", dependencies=[Depends(verify_token)])
async def list_proxies():
    proxies = db.get_all_proxies()
    return [_mask_proxy_password(p) for p in proxies]


@router.get(
    "/api/proxies/{proxy_id}",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": PROXY_NOT_FOUND_DETAIL}},
)
async def get_proxy(proxy_id: int):
    p = db.get_proxy_by_id(proxy_id)
    if not p:
        raise HTTPException(status_code=404, detail=PROXY_NOT_FOUND_DETAIL)
    return _mask_proxy_password(p)


@router.post(
    "/api/proxies",
    dependencies=[Depends(verify_token)],
    responses={400: {"description": "type must be 'http' or 'socks5'"}},
)
async def add_proxy(proxy: ProxyModel):
    data = proxy.model_dump()
    if data["type"] not in ("http", "socks5"):
        raise HTTPException(status_code=400, detail="type must be 'http' or 'socks5'")
    proxy_id = db.add_proxy(data)
    return {"id": proxy_id, "status": "success"}


@router.put(
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
    if data["type"] not in ("http", "socks5"):
        raise HTTPException(status_code=400, detail="type must be 'http' or 'socks5'")
    db.update_proxy(proxy_id, data)
    return {"status": "success"}


@router.delete(
    "/api/proxies/{proxy_id}",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": PROXY_NOT_FOUND_DETAIL}},
)
async def delete_proxy(proxy_id: int):
    if not db.get_proxy_by_id(proxy_id):
        raise HTTPException(status_code=404, detail=PROXY_NOT_FOUND_DETAIL)
    db.delete_proxy(proxy_id)
    return {"status": "success"}


@router.get("/api/proxy_bindings", dependencies=[Depends(verify_token)])
async def get_proxy_bindings():
    return db.get_proxy_bindings()


@router.post("/api/proxy_bindings", dependencies=[Depends(verify_token)])
async def update_proxy_bindings(bindings: ProxyBindingsModel):
    data = bindings.model_dump()
    db.update_proxy_bindings(
        terminal=data.get("terminal"),
        ai=data.get("ai"),
        skills=data.get("skills"),
    )
    return {"status": "success"}


@router.post("/api/proxy_bindings/clear_ai", dependencies=[Depends(verify_token)])
async def clear_ai_proxy_binding():
    """强制清除 AI 场景的代理绑定。用于修复「未勾选 AI 对话但 AI 仍走代理」的残留问题。"""
    db.upsert_system_setting("proxy_for_ai", "")
    app_logger.info("系统设置", "已清除 AI 代理绑定")
    return {"status": "success", "message": "已清除 AI 代理绑定"}


@router.get("/api/server_tree_collapsed", dependencies=[Depends(verify_token)])
async def get_server_tree_collapsed():
    s = db.get_system_settings()
    raw = s.get("server_tree_collapsed", "{}")
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


@router.put("/api/server_tree_collapsed", dependencies=[Depends(verify_token)])
async def put_server_tree_collapsed(data: dict):
    db.upsert_system_setting("server_tree_collapsed", json.dumps(data))
    return {"status": "success"}


@router.get("/api/system_settings", dependencies=[Depends(verify_token)])
async def get_system_settings():
    return db.get_system_settings()


@router.post("/api/system_settings", dependencies=[Depends(verify_token)])
async def update_system_settings(settings: SystemSettingsModel):
    data = settings.model_dump()
    for k, v in data.items():
        db.update_system_setting(k, v)
    app_logger.reload()
    app_logger.info("系统设置", "更新了日志及系统设置")
    return {"status": "success"}


@router.get("/api/logs", dependencies=[Depends(verify_token)])
async def list_logs():
    abs_log_path = get_log_path()
    if not os.path.exists(abs_log_path):
        return []

    log_files = []
    files = glob.glob(os.path.join(abs_log_path, "xterm.log*"))
    for f in sorted(files, key=os.path.getmtime, reverse=True):
        stat = os.stat(f)
        log_files.append(
            {
                "name": os.path.basename(f),
                "size": round(stat.st_size / 1024, 2),
                "mtime": stat.st_mtime,
            }
        )
    return log_files


@router.get(
    "/api/logs/content",
    dependencies=[Depends(verify_token)],
    responses={
        400: {"description": "非法文件名"},
        404: {"description": "日志文件不存在"},
        500: {"description": "读取日志失败"},
    },
)
def get_log_content(filename: str, lines: int = 500):
    abs_log_path = get_log_path()

    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="非法文件名")

    file_path = os.path.join(abs_log_path, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="日志文件不存在")

    try:
        content = []
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(0, os.SEEK_END)
            file_size = f.tell()

            buffer_size = 1024 * 16
            pos = file_size
            lines_found = 0

            while pos > 0 and lines_found <= lines:
                read_len = min(pos, buffer_size)
                pos -= read_len
                f.seek(pos)
                chunk = f.read(read_len)
                lines_found += chunk.count("\n")
                content.insert(0, chunk)

        full_text = "".join(content)
        result_lines = full_text.splitlines()
        if len(result_lines) > lines:
            result_lines = result_lines[-lines:]
        return {"content": "\n".join(result_lines)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete(
    "/api/logs",
    dependencies=[Depends(verify_token)],
    responses={500: {"description": "清空日志失败"}},
)
def clear_logs():
    abs_log_path = get_log_path()

    if not os.path.exists(abs_log_path):
        return {"status": "success"}

    try:
        files = glob.glob(os.path.join(abs_log_path, "xterm.log*"))
        for f in files:
            if os.path.basename(f) == "xterm.log":
                with open(f, "w", encoding="utf-8") as fh:
                    fh.truncate(0)
            else:
                os.remove(f)
        app_logger.info("系统设置", "已清空系统日志文件")
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/api/database/backup",
    dependencies=[Depends(verify_token)],
    responses={
        404: {"description": "数据库文件不存在"},
        500: {"description": "备份数据库失败"},
    },
)
async def backup_database():
    """将 config/xterm.db 备份到 config/backup/xterm_年月日时分秒.db"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    src_path = os.path.normpath(os.path.join(base_dir, "..", db_path))
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
        return {
            "status": "success",
            "filename": filename,
            "path": f"config/backup/{filename}",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"备份失败: {e}")


@router.get("/api/command_groups", dependencies=[Depends(verify_token)])
async def list_command_groups():
    return db.get_all_command_groups()


@router.get("/api/device_types", dependencies=[Depends(verify_token)])
async def list_device_types():
    return db.get_all_device_types()


@router.post("/api/device_types", dependencies=[Depends(verify_token)])
async def add_device_type(data: DeviceTypeModel):
    return {"id": db.add_device_type(data.dict())}


@router.put("/api/device_types/{type_id}", dependencies=[Depends(verify_token)])
async def update_device_type(type_id: int, data: DeviceTypeModel):
    db.update_device_type(type_id, data.dict())
    return {"status": "success"}


@router.delete("/api/device_types/{type_id}", dependencies=[Depends(verify_token)])
async def delete_device_type(type_id: int):
    db.delete_device_type(type_id)
    return {"status": "success"}


@router.post("/api/command_groups", dependencies=[Depends(verify_token)])
async def add_command_group(group: CommandGroupModel):
    group_id = db.add_command_group(group.name)
    return {"id": group_id, "status": "success"}


@router.put("/api/command_groups/{group_id}", dependencies=[Depends(verify_token)])
async def update_command_group(group_id: int, group: CommandGroupModel):
    db.update_command_group(group_id, group.name)
    return {"status": "success"}


@router.delete("/api/command_groups/{group_id}", dependencies=[Depends(verify_token)])
async def delete_command_group(group_id: int):
    db.delete_command_group(group_id)
    return {"status": "success"}


@router.get("/api/commands/{group_id}", dependencies=[Depends(verify_token)])
async def list_commands(group_id: int):
    return db.get_commands_by_group(group_id)


@router.post("/api/commands", dependencies=[Depends(verify_token)])
async def add_command(cmd: CommandModel):
    cmd_id = db.add_command(cmd.model_dump())
    return {"id": cmd_id, "status": "success"}


@router.put("/api/commands/{cmd_id}", dependencies=[Depends(verify_token)])
async def update_command(cmd_id: int, cmd: CommandModel):
    db.update_command(cmd_id, cmd.model_dump())
    return {"status": "success"}


@router.delete("/api/commands/{cmd_id}", dependencies=[Depends(verify_token)])
async def delete_command(cmd_id: int):
    db.delete_command(cmd_id)
    return {"status": "success"}
