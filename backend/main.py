from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from pydantic import BaseModel
from typing import List, Optional
import os
import asyncio
import json
import glob
import io
from pathlib import Path
from dotenv import load_dotenv
from ssh_handler import SSHHandler
from ai_handler import AIHandler
from database import Database
from logger import app_logger

# 加载配置
load_dotenv(dotenv_path="../.env")

app = FastAPI()

# 数据库实例
db_path = os.getenv("DB_PATH", "../config/xterm.db")
db = Database(db_path)

# 挂载静态文件
app.mount("/frontend", StaticFiles(directory="../frontend"), name="frontend")
app.mount("/static", StaticFiles(directory="../static"), name="static")

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

class AITestModel(BaseModel):
    api_key: str
    base_url: str
    model: str

class SystemSettingsModel(BaseModel):
    log_enabled: str
    log_path: str
    log_max_size: str
    log_backup_count: str
    log_level: str

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

# --- Web 路由 ---
@app.get("/")
async def get():
    with open("../frontend/index.html", "r", encoding='utf-8') as f:
        return HTMLResponse(content=f.read())

# --- 服务器管理 API ---
@app.get("/api/servers")
async def list_servers():
    return db.get_all_servers()

@app.post("/api/servers")
async def add_server(server: ServerModel):
    server_id = db.add_server(server.model_dump())
    return {"id": server_id, "status": "success"}

@app.get("/api/servers/{server_id}")
async def get_server(server_id: int):
    server = db.get_server_by_id(server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return server

@app.put("/api/servers/{server_id}")
async def update_server(server_id: int, server: ServerModel):
    db.update_server(server_id, server.model_dump())
    return {"status": "success"}

@app.delete("/api/servers/{server_id}")
async def delete_server(server_id: int):
    db.delete_server(server_id)
    return {"status": "success"}

@app.post("/api/servers/test")
async def test_server_connection(server: ServerTestModel):
    ssh = SSHHandler(
        host=server.host,
        port=server.port,
        username=server.username,
        password=server.password,
        private_key=server.private_key
    )
    if ssh.connect():
        ssh.close()
        return {"success": True, "message": "连接成功"}
    else:
        return {"success": False, "message": "连接失败，请检查主机、端口、账号或密码是否正确"}

# --- AI 配置管理 API ---
@app.get("/api/ai_endpoints")
async def list_ai():
    endpoints = db.get_all_ai_endpoints()
    # 反序列化 capabilities
    for ep in endpoints:
        if ep['capabilities']:
            try:
                ep['capabilities'] = json.loads(ep['capabilities'])
            except:
                ep['capabilities'] = ["text"]
    return endpoints

@app.get("/api/ai_endpoints/{ai_id}")
async def get_ai_endpoint(ai_id: int):
    ai = db.get_ai_endpoint_by_id(ai_id)
    if not ai:
        raise HTTPException(status_code=404, detail="AI Endpoint not found")
    if ai['capabilities']:
        ai['capabilities'] = json.loads(ai['capabilities'])
    return ai

@app.post("/api/ai_endpoints")
async def add_ai(ai: AIModel):
    ai_id = db.add_ai_endpoint(ai.model_dump())
    return {"id": ai_id, "status": "success"}

@app.put("/api/ai_endpoints/{ai_id}")
async def update_ai(ai_id: int, ai: AIModel):
    db.update_ai_endpoint(ai_id, ai.model_dump())
    return {"status": "success"}

@app.delete("/api/ai_endpoints/{ai_id}")
async def delete_ai(ai_id: int):
    db.delete_ai_endpoint(ai_id)
    return {"status": "success"}

@app.post("/api/ai_endpoints/{ai_id}/activate")
async def activate_ai(ai_id: int):
    db.set_active_ai(ai_id)
    return {"status": "success"}

@app.post("/api/ai_endpoints/test")
async def test_ai(ai: AITestModel):
    handler = AIHandler(ai.api_key, ai.base_url, ai.model, "")
    success, message = await handler.test_connection()
    return {"success": success, "message": message}

# --- 角色管理 API ---
@app.get("/api/roles")
async def list_roles():
    return db.get_all_roles()

@app.get("/api/roles/{role_id}")
async def get_role(role_id: int):
    role = db.get_role_by_id(role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    return role

@app.post("/api/roles")
async def add_role(role: RoleModel):
    role_id = db.add_role(role.model_dump())
    return {"id": role_id, "status": "success"}

@app.put("/api/roles/{role_id}")
async def update_role(role_id: int, role: RoleModel):
    db.update_role(role_id, role.model_dump())
    return {"status": "success"}

@app.delete("/api/roles/{role_id}")
async def delete_role(role_id: int):
    db.delete_role(role_id)
    return {"status": "success"}

@app.post("/api/roles/{role_id}/activate")
async def activate_role(role_id: int):
    db.set_active_role(role_id)
    app_logger.info("角色管理", f"激活角色 ID: {role_id}")
    return {"status": "success"}

# --- 系统设置 API ---
@app.get("/api/system_settings")
async def get_system_settings():
    return db.get_system_settings()

@app.post("/api/system_settings")
async def update_system_settings(settings: SystemSettingsModel):
    data = settings.model_dump()
    for k, v in data.items():
        db.update_system_setting(k, v)
    app_logger.reload()
    app_logger.info("系统设置", "更新了日志及系统设置")
    return {"status": "success"}

# --- 日志管理 API ---
@app.get("/api/logs")
async def list_logs():
    settings = db.get_system_settings()
    log_path = settings.get("log_path", "../logs")
    abs_log_path = os.path.abspath(os.path.join(os.path.dirname(__file__), log_path))
    
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

@app.get("/api/logs/content")
async def get_log_content(filename: str, lines: int = 500):
    settings = db.get_system_settings()
    log_path = settings.get("log_path", "../logs")
    abs_log_path = os.path.abspath(os.path.join(os.path.dirname(__file__), log_path))
    
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

@app.delete("/api/logs")
async def clear_logs():
    settings = db.get_system_settings()
    log_path = settings.get("log_path", "../logs")
    abs_log_path = os.path.abspath(os.path.join(os.path.dirname(__file__), log_path))
    
    if not os.path.exists(abs_log_path):
        return {"status": "success"}
        
    try:
        # 清空所有 xterm.log* 文件
        files = glob.glob(os.path.join(abs_log_path, "xterm.log*"))
        for f in files:
            # 对于正在写入的 xterm.log，清空内容而非删除，防止句柄失效
            if os.path.basename(f) == "xterm.log":
                with open(f, 'w') as _: pass
            else:
                os.remove(f)
        app_logger.info("系统设置", "已清空系统日志文件")
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- 快捷命令管理 API ---
@app.get("/api/command_groups")
async def list_command_groups():
    return db.get_all_command_groups()

@app.post("/api/command_groups")
async def add_command_group(group: CommandGroupModel):
    group_id = db.add_command_group(group.name)
    return {"id": group_id, "status": "success"}

@app.put("/api/command_groups/{group_id}")
async def update_command_group(group_id: int, group: CommandGroupModel):
    db.update_command_group(group_id, group.name)
    return {"status": "success"}

@app.delete("/api/command_groups/{group_id}")
async def delete_command_group(group_id: int):
    db.delete_command_group(group_id)
    return {"status": "success"}

@app.get("/api/commands/{group_id}")
async def list_commands(group_id: int):
    return db.get_commands_by_group(group_id)

@app.post("/api/commands")
async def add_command(cmd: CommandModel):
    cmd_id = db.add_command(cmd.model_dump())
    return {"id": cmd_id, "status": "success"}

@app.put("/api/commands/{cmd_id}")
async def update_command(cmd_id: int, cmd: CommandModel):
    db.update_command(cmd_id, cmd.model_dump())
    return {"status": "success"}

@app.delete("/api/commands/{cmd_id}")
async def delete_command(cmd_id: int):
    db.delete_command(cmd_id)
    return {"status": "success"}

# --- WebSocket 终端连接 ---
@app.websocket("/ws/ssh/{server_id}")
async def ssh_endpoint(websocket: WebSocket, server_id: int):
    await websocket.accept()
    server_info = db.get_server_by_id(server_id)
    if not server_info:
        await websocket.send_text("Server not found in database!")
        await websocket.close()
        return

    ssh_config = {
        "host": server_info["host"],
        "port": server_info["port"],
        "username": server_info["username"],
        "password": server_info["password"]
    }
    
    ssh = SSHHandler(**ssh_config)
    if not ssh.connect():
        await websocket.send_text(f"SSH Connection to {server_info['name']} Failed!")
        await websocket.close()
        return

    async def forward_to_client():
        try:
            while True:
                data = ssh.read()
                if data:
                    await websocket.send_text(data)
                await asyncio.sleep(0.01)
        except Exception:
            pass

    forward_task = asyncio.create_task(forward_to_client())

    try:
        while True:
            data = await websocket.receive_text()
            if data:
                try:
                    payload = json.loads(data)
                    if payload.get("type") == "resize":
                        ssh.resize_pty(payload["cols"], payload["rows"])
                    else:
                        ssh.write(payload.get("data", ""))
                except json.JSONDecodeError:
                    ssh.write(data)
    except WebSocketDisconnect:
        ssh.close()
        forward_task.cancel()
    except Exception:
        ssh.close()
        forward_task.cancel()

# --- WebSocket AI 连接 ---
@app.websocket("/ws/ai")
async def ai_endpoint(websocket: WebSocket):
    await websocket.accept()
    
    # 1. 获取激活的角色
    role_info = db.get_active_role()
    if not role_info:
        # 兜底：获取第一个角色或使用内置提示词
        role_info = {"system_prompt": "You are a helpful assistant.", "ai_endpoint_id": None}
    
    # 2. 获取 AI 端点
    ai_info = None
    if role_info.get("ai_endpoint_id"):
        ai_info = db.get_ai_endpoint_by_id(role_info["ai_endpoint_id"])
    
    if not ai_info:
        ai_info = db.get_active_ai_endpoint()
    
    # 3. 注入硬编码的命令执行协议（技术指令不再由用户可见的角色设置提供）
    command_protocol = """
[技术指令 - 核心准则]
1. 执行命令：当你需要执行 Shell 命令或建议用户执行命令时，必须在回复中包含以下格式的 JSON 块：
{
  "type": "command_request",
  "command": "具体的命令内容"
}
2. 每一个命令建议都必须是一个独立的 JSON 块，严禁使用 Markdown 表格、Markdown 列表或纯文本块来展示命令。
3. 交互性要求：你的回复中可以包含文字分析，但所有的操作建议必须通过上述 JSON 块提供，以便用户直接点击执行。
4. 格式规范：JSON 块可以放在 Markdown 代码块中（如 ```json ... ```），也可以直接放在正文中。
"""
    combined_prompt = role_info["system_prompt"] + "\n" + command_protocol
    
    ai_config = {
        "api_key": ai_info["api_key"] if ai_info else os.getenv("AI_API_KEY"),
        "base_url": ai_info["base_url"] if ai_info else os.getenv("AI_BASE_URL"),
        "model": ai_info["model"] if ai_info else os.getenv("AI_MODEL"),
        "system_prompt": combined_prompt
    }
    
    ai = AIHandler(**ai_config)
    
    try:
        while True:
            data = await websocket.receive_text()
            payload = json.loads(data)
            
            # 兼容处理: payload 可能是 list (旧) 或 dict (新)
            if isinstance(payload, list):
                mode = "agent"
                messages = payload
            elif isinstance(payload, dict):
                mode = payload.get("mode", "agent")
                messages = payload.get("messages", [])
            else:
                app_logger.error("AI 错误", f"无效的消息格式: {type(payload)}")
                await websocket.send_text(f"[AI Error: 无效的消息格式]")
                continue
            
            if not messages:
                continue
            
            # 动态调整提示词
            if mode == "agent":
                ai.system_prompt = role_info["system_prompt"] + "\n" + command_protocol
            else:
                ai.system_prompt = role_info["system_prompt"] + "\n(注意：当前处于 Ask 模式，请仅通过文本回答，不要要求执行任何 Shell 命令。)"

            # 日志记录 AI 请求
            user_msg = messages[-1]["content"] if messages else ""
            app_logger.info("AI 对话", f"模式: {mode}, 用户提问: {user_msg[:100]}...")
            
            full_response = ""
            async for chunk in ai.get_response_stream(messages):
                full_response += chunk
                await websocket.send_text(chunk)
            
            # 日志记录 AI 完整回复 (提升为 INFO 以便用户排查问题)
            app_logger.info("AI 对话", f"完整回复内容: {full_response}")
            
            await websocket.send_text("[DONE]")
    except WebSocketDisconnect:
        pass
    except Exception as e:
        await websocket.send_text(f"\n[AI Error: {str(e)}]\n")

# --- WebSocket 状态采集 (新增) ---
@app.websocket("/ws/stats/{server_id}")
async def stats_endpoint(websocket: WebSocket, server_id: int):
    await websocket.accept()
    server_info = db.get_server_by_id(server_id)
    if not server_info:
        await websocket.close(); return

    ssh_config = {
        "host": server_info["host"],
        "port": server_info["port"],
        "username": server_info["username"],
        "password": server_info["password"]
    }
    
    ssh = SSHHandler(**ssh_config)
    if not ssh.connect():
        await websocket.close(); return

    try:
        while True:
            # 一次性采集所有指标，使用 ### 分隔符让解析更可靠
            cmd = (
                "echo '###HOSTNAME###' && hostname && "
                "echo '###OS###' && (cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"' || uname -s) && "
                "echo '###UPTIME###' && uptime && "
                "echo '###MEM###' && free -m && "
                "echo '###DISK###' && df -h / | tail -1 && "
                "echo '###CPU_CORES###' && nproc && "
                "echo '###PROCS###' && ps -eo pmem,pcpu,comm --sort=-pcpu | head -12"
            )
            ssh.write(cmd + "\n")
            
            await asyncio.sleep(1.5)
            output = ""
            for _ in range(8):
                chunk = ssh.read()
                if chunk: output += chunk
                else: break
                await asyncio.sleep(0.2)
            
            stats = parse_stats_output(output, server_info["host"])
            await websocket.send_json(stats)
            
            await asyncio.sleep(5)  # 每 5 秒更新一次
    except Exception:
        pass
    finally:
        ssh.close()

def parse_stats_output(output, ip):
    lines = output.splitlines()
    res = {
        "hostname": "-", "os": "-", "ip": ip,
        "uptime": "-", "load": "-", "cpu": 0,
        "mem": "0 / 0", "mem_p": 0,
        "disk": "0 / 0", "disk_p": 0,
        "procs": []
    }

    try:
        # 按分隔符分段解析，避免跨段干扰
        sections = {}
        current_section = None
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('###') and stripped.endswith('###'):
                current_section = stripped[3:-3]
                sections[current_section] = []
            elif current_section:
                sections[current_section].append(line)

        # 主机名
        if 'HOSTNAME' in sections:
            for l in sections['HOSTNAME']:
                l = l.strip()
                if l and not l.startswith('#'):
                    res['hostname'] = l
                    break

        # 操作系统
        if 'OS' in sections:
            for l in sections['OS']:
                l = l.strip()
                if l and not l.startswith('#'):
                    res['os'] = l
                    break

        # Uptime / Load
        if 'UPTIME' in sections:
            for l in sections['UPTIME']:
                if 'load average' in l:
                    try:
                        res['uptime'] = l.split('up')[1].split(',')[0].strip()
                        res['load'] = l.split('load average:')[1].strip()
                    except Exception:
                        pass
                    break

        # 内存
        if 'MEM' in sections:
            for l in sections['MEM']:
                if l.startswith('Mem:'):
                    parts = l.split()
                    if len(parts) >= 3:
                        total, used = int(parts[1]), int(parts[2])
                        res['mem'] = f"{used}M / {total}M"
                        res['mem_p'] = round(used / total * 100, 1) if total else 0
                    break

        # 磁盘
        if 'DISK' in sections:
            for l in sections['DISK']:
                if '/' in l and '%' in l:
                    parts = l.split()
                    if len(parts) >= 5:
                        res['disk'] = f"{parts[2]} / {parts[1]}"
                        try:
                            res['disk_p'] = int(parts[4].replace('%', ''))
                        except Exception:
                            pass
                    break

        # CPU 核心数 & 负载换算
        cores = 1
        if 'CPU_CORES' in sections:
            for l in sections['CPU_CORES']:
                l = l.strip()
                if l.isdigit():
                    cores = max(1, int(l))
                    break
        if res['load'] != '-':
            try:
                load1 = float(res['load'].split(',')[0])
                res['cpu'] = round(min(load1 / cores * 100, 100), 1)
            except Exception:
                pass

        # 进程列表
        if 'PROCS' in sections:
            started = False
            for l in sections['PROCS']:
                if '%MEM' in l and '%CPU' in l:
                    started = True
                    continue
                if started and l.strip():
                    parts = l.split(None, 2)
                    if len(parts) >= 3:
                        try:
                            res['procs'].append({
                                "mem": parts[0] + "%",
                                "cpu": parts[1] + "%",
                                "cmd": parts[2].strip(),
                            })
                        except Exception:
                            pass

    except Exception:
        pass
    return res

# --- SFTP 管理 API ---
@app.get("/api/sftp/list")
async def sftp_list(server_id: int, path: str = "/"):
    server_info = db.get_server_by_id(server_id)
    if not server_info:
        raise HTTPException(status_code=404, detail="Server not found")
    
    ssh = SSHHandler(
        host=server_info["host"],
        port=server_info["port"],
        username=server_info["username"],
        password=server_info["password"]
    )
    
    sftp = ssh.open_sftp()
    if not sftp:
        raise HTTPException(status_code=500, detail="Failed to open SFTP session")
    
    try:
        # 如果路径为空，默认跳转到用户家目录
        if not path or path == "":
            path = sftp.normalize(".")
            
        files = []
        # 获取目录内容
        attr_list = sftp.listdir_attr(path)
        
        # 预定义排序：文件夹在前，文件名升序
        attr_list.sort(key=lambda x: (not os.path.stat.S_ISDIR(x.st_mode), x.filename.lower()))
        
        for attr in attr_list:
            is_dir = os.path.stat.S_ISDIR(attr.st_mode)
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

@app.get("/api/sftp/download")
async def sftp_download(server_id: int, path: str):
    server_info = db.get_server_by_id(server_id)
    if not server_info:
        raise HTTPException(status_code=404, detail="Server not found")
    
    ssh = SSHHandler(
        host=server_info["host"],
        port=server_info["port"],
        username=server_info["username"],
        password=server_info["password"]
    )
    
    sftp = ssh.open_sftp()
    if not sftp:
        raise HTTPException(status_code=500, detail="Failed to open SFTP session")
    
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

@app.post("/api/sftp/upload")
async def sftp_upload(
    server_id: int = Form(...),
    path: str = Form(...),
    file: UploadFile = File(...)
):
    server_info = db.get_server_by_id(server_id)
    if not server_info:
        raise HTTPException(status_code=404, detail="Server not found")
    
    ssh = SSHHandler(
        host=server_info["host"],
        port=server_info["port"],
        username=server_info["username"],
        password=server_info["password"]
    )
    
    sftp = ssh.open_sftp()
    if not sftp:
        raise HTTPException(status_code=500, detail="Failed to open SFTP session")
    
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

@app.post("/api/sftp/rename")
async def sftp_rename(data: SftpRenameModel):
    server_info = db.get_server_by_id(data.server_id)
    ssh = SSHHandler(host=server_info["host"], port=server_info["port"], username=server_info["username"], password=server_info["password"])
    sftp = ssh.open_sftp()
    try:
        sftp.rename(data.old_path, data.new_path)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if sftp: sftp.close()
        ssh.close()

@app.post("/api/sftp/chmod")
async def sftp_chmod(data: SftpChmodModel):
    server_info = db.get_server_by_id(data.server_id)
    ssh = SSHHandler(host=server_info["host"], port=server_info["port"], username=server_info["username"], password=server_info["password"])
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

@app.delete("/api/sftp/delete")
async def sftp_delete(data: SftpDeleteModel):
    server_info = db.get_server_by_id(data.server_id)
    ssh = SSHHandler(host=server_info["host"], port=server_info["port"], username=server_info["username"], password=server_info["password"])
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
            except:
                pass
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if sftp: sftp.close()
        ssh.close()

@app.get("/api/sftp/read")
async def sftp_read(server_id: int, path: str):
    server_info = db.get_server_by_id(server_id)
    ssh = SSHHandler(host=server_info["host"], port=server_info["port"], username=server_info["username"], password=server_info["password"])
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

@app.post("/api/sftp/save")
async def sftp_save(data: SftpSaveModel):
    server_info = db.get_server_by_id(data.server_id)
    ssh = SSHHandler(host=server_info["host"], port=server_info["port"], username=server_info["username"], password=server_info["password"])
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

@app.post("/api/sftp/create")
async def sftp_create(data: SftpCreateModel):
    server_info = db.get_server_by_id(data.server_id)
    ssh = SSHHandler(host=server_info["host"], port=server_info["port"], username=server_info["username"], password=server_info["password"])
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
