import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List


def _extract_error_text(result: Dict[str, Any]) -> str:
    logs = result.get("logs") or []
    parts: List[str] = []
    for item in logs:
        stderr = str(item.get("stderr") or "").strip()
        stdout = str(item.get("stdout") or "").strip()
        if stderr:
            parts.append(stderr)
        elif not item.get("ok") and stdout:
            parts.append(stdout)
    return "\n".join(parts).strip().lower()


def classify_failure(result: Dict[str, Any]) -> Dict[str, str]:
    signature = (result.get("error_signature") or "unknown_error").strip() or "unknown_error"
    logs_text = _extract_error_text(result)
    category = "runtime"
    if "timeout" in logs_text or "timed out" in logs_text:
        category = "timeout"
    elif "permission denied" in logs_text or "eacces" in logs_text:
        category = "permission"
    elif "syntaxerror" in logs_text or "parse error" in logs_text:
        category = "syntax"
    elif "connection refused" in logs_text or "network is unreachable" in logs_text:
        category = "network"
    elif "migration" in signature or "schema" in signature or "sql" in logs_text:
        category = "migration"
    elif "verify" in signature:
        category = "verification"
    elif "health_check" in signature or "health check" in logs_text:
        category = "health_check"

    suggestion_map = {
        "timeout": "缩小任务范围，拆分子任务并提高超时时间后重试。",
        "permission": "检查目标文件/命令权限，必要时降级为需审批人工执行。",
        "syntax": "先执行静态检查（lint/compile），再进入热加载。",
        "network": "检查目标网络连通性与白名单策略，确认再重试。",
        "migration": "先备份数据库并在低峰执行，准备 down 脚本后重试。",
        "verification": "补充更精确的验证命令，避免假失败或漏报。",
        "health_check": "确认健康探针覆盖关键路径，并准备一键回滚。",
        "runtime": "查看阶段日志并缩小变更面，逐步验证后重试。",
    }
    summary = f"错误分类: {category}；错误签名: {signature}。"
    return {
        "error_category": category,
        "error_signature": signature,
        "summary": summary,
        "action_suggestion": suggestion_map.get(category, suggestion_map["runtime"]),
    }


def build_unfixable_report(task: Dict[str, Any], analysis: Dict[str, str], retry_count: int, max_retries: int) -> str:
    now = datetime.now(timezone.utc).isoformat()
    return (
        f"# 无法自动修复报告\n\n"
        f"- 任务ID: {task.get('id')}\n"
        f"- 任务标题: {task.get('title')}\n"
        f"- 风险等级: {task.get('risk_level')}\n"
        f"- 重试次数: {retry_count}/{max_retries}\n"
        f"- 触发时间: {now}\n\n"
        f"## 失败摘要\n"
        f"{analysis.get('summary', '-')}\n\n"
        f"## 错误签名\n"
        f"`{analysis.get('error_signature', '-')}`\n\n"
        f"## 建议处置\n"
        f"{analysis.get('action_suggestion', '-')}\n\n"
        f"## 人工接管建议\n"
        f"1. 复核任务目标与验收标准是否可实现\n"
        f"2. 缩小变更范围并拆分子任务\n"
        f"3. 必要时改为人工变更后由系统回放验证\n"
    )


def checksum_of_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()
