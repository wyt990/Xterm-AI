from fastapi import APIRouter, Depends, Form, HTTPException
from typing import Annotated

from ssh_handler import SSHHandler
from logger import app_logger
from core import (
    LOG_SCOPE_SSH_TEST,
    SERVER_NOT_FOUND_DETAIL,
    ServerDocModel,
    ServerModel,
    ServerTestModel,
    _get_ssh_config,
    _get_terminal_proxy,
    db,
    verify_token,
)


router = APIRouter()


@router.get("/api/servers", dependencies=[Depends(verify_token)])
async def list_servers():
    return db.get_all_servers()


@router.post("/api/servers", dependencies=[Depends(verify_token)])
async def add_server(server: ServerModel):
    server_id = db.add_server(server.model_dump())
    return {"id": server_id, "status": "success"}


@router.get("/api/servers/recent", dependencies=[Depends(verify_token)])
async def list_recent_servers(limit: int = 20):
    """最近连接服务器（按 last_connected_at 倒序）"""
    if limit < 1:
        limit = 1
    if limit > 100:
        limit = 100
    return db.get_recent_servers(limit=limit)


@router.post(
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


@router.delete("/api/servers/recent", dependencies=[Depends(verify_token)])
async def clear_recent_servers():
    """清空最近连接记录（保留服务器资产数据）"""
    db.clear_recent_connections()
    return {"status": "success"}


@router.get(
    "/api/servers/{server_id}",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": SERVER_NOT_FOUND_DETAIL}},
)
async def get_server(server_id: int):
    server = db.get_server_by_id(server_id)
    if not server:
        raise HTTPException(status_code=404, detail=SERVER_NOT_FOUND_DETAIL)
    return server


@router.put("/api/servers/{server_id}", dependencies=[Depends(verify_token)])
async def update_server(server_id: int, server: ServerModel):
    db.update_server(server_id, server.model_dump())
    return {"status": "success"}


@router.delete("/api/servers/{server_id}", dependencies=[Depends(verify_token)])
async def delete_server(server_id: int):
    db.delete_server(server_id)
    return {"status": "success"}


@router.get(
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


@router.put(
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


@router.post("/api/servers/test", dependencies=[Depends(verify_token)])
async def test_server_connection(server: ServerTestModel):
    app_logger.info(
        LOG_SCOPE_SSH_TEST,
        f"收到测试请求: host={server.host} port={server.port} user={server.username} auth={'key' if server.private_key else 'pwd'}",
    )
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
        app_logger.info(
            LOG_SCOPE_SSH_TEST,
            f"使用终端代理: {proxy.get('type')} {proxy.get('host')}:{proxy.get('port')}",
        )
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


@router.get("/api/servers/{server_id}/stats/history", dependencies=[Depends(verify_token)])
async def get_server_stats_history(server_id: int, minutes: int = 30):
    """获取过去 X 分钟的历史指标数据"""
    return db.get_stats_history(server_id, minutes)


@router.delete("/api/servers/stats/history/all", dependencies=[Depends(verify_token)])
async def clear_all_stats_history():
    """清除所有服务器的状态历史记录"""
    db.clear_all_stats_history()
    return {"status": "success", "message": "已清除所有服务器的状态记录"}


@router.delete("/api/servers/{server_id}/stats/history", dependencies=[Depends(verify_token)])
async def clear_server_stats_history(server_id: int):
    """清除指定服务器的所有状态历史记录"""
    db.clear_stats_for_server(server_id)
    return {"status": "success", "message": "已清除该服务器的状态记录"}


@router.post(
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
        ssh.write(f"kill -9 {pid}\n")
        return {"status": "success", "message": f"Sent kill signal to PID {pid}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        ssh.close()
