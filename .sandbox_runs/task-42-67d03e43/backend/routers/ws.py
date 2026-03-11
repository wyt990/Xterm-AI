from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from typing import Optional
import asyncio
import json
import os
from datetime import datetime

from ai_handler import AIHandler
from core import _get_ssh_config, db, verify_ws_token
from logger import app_logger
from ssh_handler import SSHHandler


router = APIRouter()
EVOLUTION_DIALOG_DEFAULT_PROMPT = """
你是“自进化控制平面助手”。

目标：
1) 帮助用户设计、执行和复盘系统自进化任务（前端、后端、插件、数据库、任务编排）。
2) 以产品化思维给出可执行方案、风险、回滚与验收标准。
3) 默认输出自然语言与结构化要点，不强制输出 JSON 指令格式。

行为约束：
- 不编造执行结果；不确定时明确说明并给出验证步骤。
- 优先给出最小可行改动与安全建议（权限、审计、回滚、重试上限）。
- 回答尽量简洁清晰，必要时给出分步清单。
""".strip()
EVOLUTION_DIALOG_OUTPUT_PROTOCOL = """
[输出协议 - 必须遵守]
你必须返回严格 JSON（不要输出任何额外文本、前后缀或 Markdown 代码块）。
JSON 结构如下：
{
  "version": "v1",
  "messages": [
    {
      "type": "text",
      "text": "给用户的文本回复"
    }
  ]
}

允许的消息类型：
1) text
   - 必填: type, text
2) task_card
   - 必填: type, title
   - 可选: task_id, description, status
3) command_card
   - 必填: type, command
   - 可选: risk, note
4) status_card
   - 必填: type, title
   - 可选: task_id, status, detail

每条消息可选 actions 字段（数组），用于前端一键执行：
actions: [
  {
    "type": "create_task | open_task_detail | run_task_async",
    "label": "按钮文案",
    "task_id": 123,
    "payload": { "title": "...", "description": "...", "task_type": "fix", "scope": "backend" }
  }
]
说明：
- create_task: 需要 payload，前端会创建任务并自动跳转任务中心详情。
- open_task_detail: 需要 task_id（可省略并回退当前消息 task_id）。
- run_task_async: 需要 task_id（可省略并回退当前消息 task_id）。

要求：
- messages 必须是数组，至少 1 条。
- 不确定是否需要卡片时，至少输出一条 text。
- 禁止输出不可解析内容。
""".strip()


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


@router.websocket("/ws/ssh/{server_id}")
async def ssh_endpoint(websocket: WebSocket, server_id: int):
    app_logger.info("终端连接", f"WebSocket 请求 server_id={server_id}")
    ssh = await _prepare_ssh_websocket(websocket, server_id)
    if not ssh:
        return
    await _run_ssh_websocket_bridge(ssh, websocket)


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
    app_logger.info(
        "终端连接",
        f"磁贴连接: name={server_info.get('name')} host={cfg['host']} port={cfg['port']} user={cfg['username']} auth={auth} proxy={bool(cfg.get('proxy'))}",
    )

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
            skill_block, part_len, overflow_block = _format_skill_block(
                skill, max_skill_content, max_total_skills, total_len
            )
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
        return "agent", payload, {}
    if isinstance(payload, dict):
        return payload.get("mode", "agent"), payload.get("messages", []), payload
    return None, None, {}


async def _parse_ws_ai_payload(websocket: WebSocket, data: str):
    payload = json.loads(data)
    mode, messages, meta = _parse_mode_and_messages(payload)
    if mode is None:
        app_logger.error("AI 错误", f"无效的消息格式: {type(payload)}")
        await websocket.send_text("[AI Error: 无效的消息格式]")
        return None
    if not messages:
        return None
    return mode, messages, meta


async def _run_ai_loop(
    websocket: WebSocket,
    role_info: dict,
    env_constraints: str,
    server_doc_block: str,
    skills_block: str,
    command_protocol: str,
    profile: str,
):
    combined_prompt = f"{role_info['system_prompt']}\n\n{env_constraints}\n\n{server_doc_block}{skills_block}{command_protocol}"

    def _build_runtime_ai(mode: str, meta: dict):
        if profile == "evolution_dialog":
            dialog_prompt = str(meta.get("dialog_system_prompt") or "").strip() or EVOLUTION_DIALOG_DEFAULT_PROMPT
            runtime_prompt = f"{dialog_prompt}\n\n{EVOLUTION_DIALOG_OUTPUT_PROTOCOL}"
            evo_role = {"system_prompt": dialog_prompt, "ai_endpoint_id": None}
            ai = _build_ai_handler(evo_role, runtime_prompt)
            ai.system_prompt = (
                runtime_prompt
                if mode == "agent"
                else runtime_prompt + "\n(注意：当前处于 Ask 模式。)"
            )
            return ai
        ai = _build_ai_handler(role_info, combined_prompt)
        base = f"{role_info['system_prompt']}\n\n{env_constraints}\n\n{server_doc_block}{skills_block}"
        ai.system_prompt = (
            base + command_protocol
            if mode == "agent"
            else base + "\n(注意：当前处于 Ask 模式，请仅通过文本回答，不要要求执行任何 Shell 命令。)"
        )
        return ai

    while True:
        data = await websocket.receive_text()
        parsed = await _parse_ws_ai_payload(websocket, data)
        if not parsed:
            continue
        mode, messages, meta = parsed

        ai = _build_runtime_ai(mode, meta)

        full_response = ""
        async for chunk in ai.get_response_stream(messages):
            full_response += chunk
            await websocket.send_text(chunk)
        app_logger.info("AI 对话", f"完整回复内容: {full_response}")
        await websocket.send_text("[DONE]")


@router.websocket("/ws/ai")
async def ai_endpoint(
    websocket: WebSocket,
    role_id: Optional[int] = None,
    device_type: str = "unknown",
    server_name: str = "未选定服务器",
    server_id: Optional[int] = None,
    profile: str = "ops",
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
        await _run_ai_loop(websocket, role_info, env_constraints, server_doc_block, skills_block, command_protocol, profile)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        await websocket.send_text(f"\n[AI Error: {str(e)}]\n")


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
    for _ in range(15):
        await asyncio.sleep(0.2)
        chunk = ssh.read()
        if chunk:
            output += chunk
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
        await asyncio.sleep(5)


@router.websocket("/ws/stats/{server_id}")
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
