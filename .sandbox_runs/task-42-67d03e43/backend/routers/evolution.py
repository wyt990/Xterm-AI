import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Annotated, Any, Dict, Optional
from threading import Lock

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from core import db, verify_token
from evolution_queue import EvolutionTaskQueue
from evolution_phase_d import build_unfixable_report, checksum_of_text, classify_failure
from evolution_runtime import EvolutionRuntime, dump_logs_to_file
from evolution_scheduler import EvolutionScheduler
from plugin_manager import PluginManager


router = APIRouter()
TASK_NOT_FOUND = "Task not found"
PLUGIN_NOT_FOUND = "Plugin not found"
TASK_TEMPLATE_NOT_FOUND = "Task template not found"
UPLOAD_NOT_FOUND = "Upload not found"
runtime = EvolutionRuntime()
queue_manager: Optional[EvolutionTaskQueue] = None
scheduler_manager: Optional[EvolutionScheduler] = None
plugin_manager = PluginManager(project_root=Path(__file__).resolve().parents[2])
UPLOAD_ROOT = (Path(__file__).resolve().parents[2] / "runtime" / "ai_uploads").resolve()
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
upload_state_lock = Lock()
upload_states: Dict[str, Dict[str, Any]] = {}


class EvolutionTaskCreateModel(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    source: str = "user"
    task_type: str = "fix"
    scope: str = "backend"
    risk_level: str = "low"
    acceptance_criteria: str = ""
    rollback_plan: str = ""
    payload: Dict[str, Any] = {}
    max_retries: int = 100
    created_by: str = "system"


class EvolutionTaskUpdateModel(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = None
    source: Optional[str] = None
    task_type: Optional[str] = None
    scope: Optional[str] = None
    risk_level: Optional[str] = None
    acceptance_criteria: Optional[str] = None
    rollback_plan: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    max_retries: Optional[int] = Field(default=None, ge=1)


class EvolutionRunModel(BaseModel):
    result: str = "success"  # success | failed
    detail: str = ""
    error_signature: Optional[str] = None
    trigger_type: str = "manual"
    result_payload: Dict[str, Any] = {}


class EvolutionRejectModel(BaseModel):
    reason: str = ""


class EvolutionDemoSeedModel(BaseModel):
    clear_existing: bool = False


class EvolutionSchedulerConfigModel(BaseModel):
    enabled: bool = True
    interval_sec: int = 30
    max_tasks_per_tick: int = 3
    retry_delay_sec: int = 60


class PluginInstallModel(BaseModel):
    manifest: Dict[str, Any]
    files: Dict[str, str] = {}


class PluginTemplateTaskModel(BaseModel):
    template_key: str
    overrides: Dict[str, Any] = {}
    created_by: str = "plugin"


class EvolutionMigrationApplyModel(BaseModel):
    migration_name: str
    up_sql: str
    down_sql: str = ""
    applied_by: str = "system"


class AIDialogUploadInitModel(BaseModel):
    session_id: str
    file_name: str = Field(..., min_length=1, max_length=255)
    file_size: int = Field(..., ge=1)
    chunk_size: int = Field(default=1024 * 512, ge=64 * 1024, le=2 * 1024 * 1024)
    mime_type: str = ""


def _normalize_risk_level(risk: str) -> str:
    val = (risk or "low").lower()
    if val not in ("low", "medium", "high"):
        return "low"
    return val


def _requires_approval_for_risk(risk: str) -> int:
    return 1 if risk in ("medium", "high") else 0


def _validate_task_for_run(task: dict) -> Optional[str]:
    if not task:
        return TASK_NOT_FOUND
    if task["status"] in ("success", "cancelled", "rolled_back"):
        return "Task already finished"
    if task["status"] == "blocked_manual":
        return "Task blocked and needs human action"
    if task.get("requires_approval") and task.get("approval_status") != "approved":
        return "Task requires approval before run"
    return None


def _validate_task_for_edit(task: dict) -> Optional[str]:
    if not task:
        return TASK_NOT_FOUND
    if (task.get("status") or "") not in ("pending_approval", "cancelled"):
        return "Only pending_approval/cancelled task can be edited"
    return None


def _validate_task_for_cancel(task: dict) -> Optional[str]:
    if not task:
        return TASK_NOT_FOUND
    status = (task.get("status") or "")
    if status in ("running", "queued"):
        return "Running or queued task cannot be cancelled"
    if status in ("success", "rolled_back", "cancelled"):
        return "Task cannot be cancelled in current status"
    return None


def _validate_task_for_enable(task: dict) -> Optional[str]:
    if not task:
        return TASK_NOT_FOUND
    status = (task.get("status") or "")
    if status != "cancelled":
        return "Only cancelled task can be enabled"
    return None


def _validate_task_for_delete(task: dict) -> Optional[str]:
    if not task:
        return TASK_NOT_FOUND
    status = (task.get("status") or "")
    if status in ("running", "queued"):
        return "Running or queued task cannot be deleted"
    if status in ("success", "rolled_back"):
        return "Finished task cannot be deleted; consider cancel/archive policy"
    return None


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    s = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def _scheduler_config():
    return db.get_evolution_scheduler_config()


def _finalize_success(task_id: int, operator: str, trigger_type: str, log_file: str, result: Dict[str, Any], migration: Dict[str, Any]) -> Dict[str, Any]:
    if migration.get("enabled") and migration.get("ok"):
        db.add_schema_migration_record(
            {
                "migration_name": migration.get("name", "unnamed_migration"),
                "checksum": checksum_of_text(migration.get("up_sql", "")),
                "status": "applied",
                "applied_by": operator,
                "detail": migration,
            }
        )
    db.update_evolution_task(
        task_id,
        {
            "status": "success",
            "error_signature": None,
            "error_repeat_count": 0,
            "needs_human_action": 0,
            "last_run_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        },
    )
    db.add_evolution_run(
        task_id=task_id,
        run_status="success",
        trigger_type=trigger_type,
        detail=f"Phase D 执行成功，日志: {log_file}",
        result_json=result,
        operator=operator,
    )
    return {"status": "success", "task_status": "success", "log_file": log_file}


def _finalize_failure(
    task: Dict[str, Any],
    task_id: int,
    operator: str,
    trigger_type: str,
    log_file: str,
    result: Dict[str, Any],
    final_status: str,
    analysis: Dict[str, Any],
    error_signature: Optional[str],
    migration: Dict[str, Any],
) -> Dict[str, Any]:
    retry_count = int(task.get("retry_count", 0)) + 1
    max_retries = int(task.get("max_retries", 100))
    blocked = retry_count >= max_retries
    new_status = _resolve_failed_status(final_status, blocked)
    final_report = _resolve_final_report(task, analysis, blocked, retry_count, max_retries)
    if analysis:
        db.add_evolution_experience(
            {
                "task_id": task_id,
                "error_category": analysis.get("error_category", "unknown"),
                "error_signature": analysis.get("error_signature"),
                "summary": analysis.get("summary", ""),
                "action_suggestion": analysis.get("action_suggestion", ""),
                "raw": {"result": result, "task": task},
            }
        )
    report_id = None
    if blocked:
        report_id = db.add_evolution_failure_report(
            {
                "task_id": task_id,
                "report_title": f"任务#{task_id} 无法自动修复报告",
                "report_markdown": final_report,
                "notify_channel": "ui",
                "notify_status": "sent",
            }
        )
    db.update_evolution_task(
        task_id,
        {
            "status": new_status,
            "retry_count": retry_count,
            "error_signature": error_signature,
            "error_repeat_count": int(task.get("error_repeat_count", 0)) + (1 if error_signature else 0),
            "needs_human_action": 1 if (blocked or new_status == "rolled_back") else 0,
            "final_report": final_report,
            "last_run_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        },
    )
    db.add_evolution_run(
        task_id=task_id,
        run_status="failed" if new_status != "rolled_back" else "rolled_back",
        trigger_type=trigger_type,
        detail=f"Phase D 执行失败/回滚，日志: {log_file}",
        result_json=result,
        operator=operator,
    )
    if migration.get("enabled"):
        db.add_schema_migration_record(
            {
                "migration_name": migration.get("name", "unnamed_migration"),
                "checksum": checksum_of_text(migration.get("up_sql", "")),
                "status": "rolled_back" if new_status == "rolled_back" else "failed",
                "applied_by": operator,
                "detail": migration,
                "rolled_back_at": datetime.now(timezone.utc).isoformat() if new_status == "rolled_back" else None,
            }
        )
    return {
        "status": "success",
        "task_status": new_status,
        "retry_count": retry_count,
        "max_retries": max_retries,
        "log_file": log_file,
        "error_category": analysis.get("error_category") if analysis else None,
        "report_id": report_id,
    }


def _find_task_template(templates: list, template_key: str):
    for item in templates:
        if (item.get("key") or "") == template_key:
            return item
    return None


def _resolve_failed_status(final_status: str, blocked: bool) -> str:
    if final_status == "rolled_back":
        return "rolled_back"
    if blocked:
        return "blocked_manual"
    return "failed"


def _resolve_final_report(task: Dict[str, Any], analysis: Dict[str, Any], blocked: bool, retry_count: int, max_retries: int) -> str:
    if blocked:
        return build_unfixable_report(task, analysis, retry_count, max_retries)
    if analysis:
        return f"{analysis.get('summary')} 建议：{analysis.get('action_suggestion')}"
    return task.get("final_report")


def _build_plugin_task_data(plugin_id: str, template: Dict[str, Any], created_by: str, overrides: Dict[str, Any]) -> Dict[str, Any]:
    risk_level = _normalize_risk_level((template.get("risk_level") or "medium"))
    requires_approval = _requires_approval_for_risk(risk_level)
    status = "pending_approval" if requires_approval else "approved"
    approval_status = "pending" if requires_approval else "approved"
    merged_payload = {}
    merged_payload.update(template.get("payload") or {})
    merged_payload.update(overrides or {})
    max_retries = int(template.get("max_retries") or 100)
    return {
        "title": template.get("title") or f"{plugin_id}:{template.get('key')}",
        "description": template.get("description") or "",
        "source": "plugin",
        "task_type": template.get("task_type") or "feature",
        "scope": "plugin",
        "risk_level": risk_level,
        "status": status,
        "requires_approval": requires_approval,
        "approval_status": approval_status,
        "max_retries": max(1, max_retries),
        "acceptance_criteria": template.get("acceptance_criteria") or "",
        "rollback_plan": template.get("rollback_plan") or "",
        "payload": merged_payload,
        "created_by": created_by or "plugin",
    }


def _calc_error_repeat_count(task: Dict[str, Any], current_sig: Optional[str]) -> int:
    old_sig = (task.get("error_signature") or "").strip() or None
    old_count = int(task.get("error_repeat_count", 0))
    if current_sig and old_sig == current_sig:
        return old_count + 1
    if current_sig:
        return 1
    return 0


def _demo_seed_tasks(operator: str) -> list:
    return [
        {
            "title": "示例任务：修复任务中心筛选异常",
            "description": "当状态筛选切换时，列表偶发不刷新。",
            "source": "user",
            "task_type": "fix",
            "scope": "frontend",
            "risk_level": "low",
            "status": "approved",
            "requires_approval": 0,
            "approval_status": "approved",
            "acceptance_criteria": "切换筛选条件后列表稳定刷新",
            "rollback_plan": "回退筛选逻辑变更",
            "payload": {"module": "task-center"},
            "created_by": operator,
        },
        {
            "title": "示例任务：新增交换机端口巡检插件",
            "description": "按 5 分钟周期检查端口状态并生成报告。",
            "source": "plugin",
            "task_type": "feature",
            "scope": "plugin",
            "risk_level": "medium",
            "status": "pending_approval",
            "requires_approval": 1,
            "approval_status": "pending",
            "acceptance_criteria": "可定时巡检并输出报告",
            "rollback_plan": "禁用并卸载插件",
            "payload": {"plugin_id": "switch-port-guardian"},
            "created_by": operator,
        },
        {
            "title": "示例任务：后端执行链路重构",
            "description": "优化任务执行状态写入顺序。",
            "source": "system",
            "task_type": "refactor",
            "scope": "backend",
            "risk_level": "low",
            "status": "success",
            "requires_approval": 0,
            "approval_status": "approved",
            "acceptance_criteria": "运行状态无丢失",
            "rollback_plan": "恢复原状态写入流程",
            "payload": {"files": ["backend/routers/evolution.py"]},
            "created_by": operator,
        },
        {
            "title": "示例任务：数据库索引补充",
            "description": "为任务列表查询新增索引。",
            "source": "user",
            "task_type": "self_modify_db_schema",
            "scope": "db",
            "risk_level": "high",
            "status": "blocked_manual",
            "requires_approval": 1,
            "approval_status": "approved",
            "retry_count": 100,
            "max_retries": 100,
            "error_signature": "migration_lock_timeout",
            "error_repeat_count": 3,
            "final_report": "示例：迁移锁等待超时，建议低峰期执行并先做全量备份。",
            "needs_human_action": 1,
            "acceptance_criteria": "列表查询耗时下降",
            "rollback_plan": "回滚 migration 文件",
            "payload": {"migration": "202603101530_add_task_indexes.sql"},
            "created_by": operator,
        },
        {
            "title": "示例任务：统一状态标签样式",
            "description": "任务中心状态标签颜色统一。",
            "source": "user",
            "task_type": "feature",
            "scope": "frontend",
            "risk_level": "low",
            "status": "failed",
            "requires_approval": 0,
            "approval_status": "approved",
            "retry_count": 2,
            "max_retries": 100,
            "error_signature": "css_build_failed",
            "error_repeat_count": 2,
            "acceptance_criteria": "标签颜色符合设计规范",
            "rollback_plan": "回退 style 变更",
            "payload": {"stylesheet": "frontend/css/layout.css"},
            "created_by": operator,
        },
    ]


def _phase_d_ops_templates() -> list:
    return [
        {
            "template_id": "ops.open_port_schedule",
            "name": "定时开放端口并验证",
            "description": "按计划连接目标服务器，开放指定端口并做健康检查。",
            "task": {
                "title": "定时开放端口并验证",
                "source": "plugin",
                "task_type": "feature",
                "scope": "backend",
                "risk_level": "medium",
                "max_retries": 100,
                "acceptance_criteria": "端口开放后探针可连通",
                "rollback_plan": "执行端口关闭命令并恢复防火墙规则",
                "payload": {
                    "commands": ["echo apply firewall rule"],
                    "verify_commands": ["echo verify firewall rule"],
                    "hot_reload_commands": ["echo reload backend service"],
                    "health_check_commands": ["echo health check api"],
                    "rollback_commands": ["echo rollback firewall rule"],
                },
            },
        },
        {
            "template_id": "ops.switch_port_auto_recover",
            "name": "交换机端口巡检与自动恢复",
            "description": "周期巡检端口状态，发现 down 时自动 up 并产出报告。",
            "task": {
                "title": "交换机端口巡检与自动恢复",
                "source": "plugin",
                "task_type": "feature",
                "scope": "plugin",
                "risk_level": "high",
                "max_retries": 100,
                "acceptance_criteria": "报告产出成功，down 端口恢复后状态为 up",
                "rollback_plan": "恢复巡检前端口配置并输出人工接管提示",
                "payload": {
                    "commands": ["echo inspect switch ports", "echo auto recover down ports"],
                    "verify_commands": ["echo verify port status"],
                    "hot_reload_commands": ["echo reload plugin runtime"],
                    "health_check_commands": ["echo health check plugin output"],
                    "rollback_commands": ["echo rollback switch config"],
                },
            },
        },
    ]


def _sanitize_file_name(name: str) -> str:
    safe = os.path.basename(name or "").strip().replace("\\", "_").replace("/", "_")
    return safe[:255] or "upload.bin"


def _upload_dir(upload_id: str) -> Path:
    return UPLOAD_ROOT / upload_id


def _state_brief(state: Dict[str, Any]) -> Dict[str, Any]:
    total_chunks = int(state.get("total_chunks", 0))
    uploaded_chunks = len(state.get("uploaded_chunks", set()))
    progress = int((uploaded_chunks / total_chunks) * 100) if total_chunks else 0
    return {
        "upload_id": state.get("upload_id"),
        "session_id": state.get("session_id"),
        "file_name": state.get("file_name"),
        "file_size": state.get("file_size"),
        "chunk_size": state.get("chunk_size"),
        "total_chunks": total_chunks,
        "uploaded_chunks": uploaded_chunks,
        "uploaded_bytes": int(state.get("uploaded_bytes", 0)),
        "progress": progress,
        "status": state.get("status", "uploading"),
        "mime_type": state.get("mime_type", ""),
    }


def _uploaded_chunk_indexes(state: Dict[str, Any]) -> list[int]:
    raw = state.get("uploaded_chunks", set())
    if isinstance(raw, set):
        return sorted(int(x) for x in raw)
    if isinstance(raw, list):
        return sorted(int(x) for x in raw)
    return []


def _run_async_core(task_id: int, operator: str, trigger_type: str = "async") -> Dict[str, Any]:
    task = db.get_evolution_task_by_id(task_id)
    if not task:
        return {"status": "error", "detail": TASK_NOT_FOUND}
    block_reason = _validate_task_for_run(task)
    if block_reason:
        return {"status": "error", "detail": block_reason}
    db.update_evolution_task(task_id, {"status": "running"})

    result = runtime.execute_task(task)
    log_file = dump_logs_to_file(result)
    final_status = result.get("task_status", "failed")
    ok = bool(result.get("ok", False))
    analysis = classify_failure(result) if not ok else {}
    error_signature = analysis.get("error_signature") if analysis else result.get("error_signature")
    migration = result.get("migration") or {}

    if ok and final_status == "success":
        return _finalize_success(task_id, operator, trigger_type, log_file, result, migration)
    return _finalize_failure(
        task=task,
        task_id=task_id,
        operator=operator,
        trigger_type=trigger_type,
        log_file=log_file,
        result=result,
        final_status=final_status,
        analysis=analysis,
        error_signature=error_signature,
        migration=migration,
    )


def _queue_execute(task_id: int):
    _run_async_core(task_id=task_id, operator="queue-worker", trigger_type="queue")


def start_evolution_queue():
    global queue_manager
    if queue_manager is None:
        queue_manager = EvolutionTaskQueue(
            execute_fn=_queue_execute,
            max_workers=int(os.getenv("EVOLUTION_QUEUE_WORKERS", "1")),
            max_size=int(os.getenv("EVOLUTION_QUEUE_MAX_SIZE", "100")),
        )
    queue_manager.start()


def _run_scheduler_once() -> Dict[str, int]:
    cfg = db.get_evolution_scheduler_config()
    max_tasks = max(1, int(cfg.get("max_tasks_per_tick", 3)))
    retry_delay = max(0, int(cfg.get("retry_delay_sec", 60)))
    tasks = db.get_schedulable_evolution_tasks(max_tasks=max_tasks)
    picked = len(tasks)
    enqueued = 0
    skipped = 0
    now = datetime.now(timezone.utc)
    if queue_manager is None:
        start_evolution_queue()
    for task in tasks:
        if (task.get("status") or "") == "failed":
            ref_time = _parse_time(task.get("last_run_at")) or _parse_time(task.get("updated_at"))
            if ref_time and (now - ref_time).total_seconds() < retry_delay:
                skipped += 1
                continue
        try:
            assert queue_manager is not None
            queue_manager.enqueue(int(task["id"]))
            db.update_evolution_task(int(task["id"]), {"status": "queued"})
            enqueued += 1
        except Exception:
            skipped += 1
    return {"picked": picked, "enqueued": enqueued, "skipped": skipped}


def start_evolution_scheduler():
    global scheduler_manager
    if scheduler_manager is None:
        scheduler_manager = EvolutionScheduler(
            config_getter=_scheduler_config,
            run_once_fn=_run_scheduler_once,
        )
    scheduler_manager.start()


def stop_evolution_queue():
    if queue_manager:
        queue_manager.stop()


def stop_evolution_scheduler():
    if scheduler_manager:
        scheduler_manager.stop()


AuthUser = Annotated[dict, Depends(verify_token)]


@router.get("/api/evolution/tasks", dependencies=[Depends(verify_token)])
async def list_tasks(status: Optional[str] = None, limit: int = 100, offset: int = 0):
    return db.get_all_evolution_tasks(status=status, limit=limit, offset=offset)


@router.get(
    "/api/evolution/tasks/{task_id}",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": TASK_NOT_FOUND}},
)
async def get_task(task_id: int):
    task = db.get_evolution_task_by_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=TASK_NOT_FOUND)
    return task


@router.get(
    "/api/evolution/tasks/{task_id}/runs",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": TASK_NOT_FOUND}},
)
async def get_task_runs(task_id: int, limit: int = 50, order: str = "desc"):
    task = db.get_evolution_task_by_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=TASK_NOT_FOUND)
    return db.get_runs_by_task_id(task_id, limit=limit, order=order)


@router.post("/api/evolution/tasks", dependencies=[Depends(verify_token)])
async def create_task(data: EvolutionTaskCreateModel):
    risk_level = _normalize_risk_level(data.risk_level)
    requires_approval = _requires_approval_for_risk(risk_level)
    status = "pending_approval" if requires_approval else "approved"
    approval_status = "pending" if requires_approval else "approved"
    max_retries = max(1, int(data.max_retries or 100))
    task_id = db.add_evolution_task(
        {
            "title": data.title,
            "description": data.description,
            "source": data.source,
            "task_type": data.task_type,
            "scope": data.scope,
            "risk_level": risk_level,
            "status": status,
            "requires_approval": requires_approval,
            "approval_status": approval_status,
            "max_retries": max_retries,
            "acceptance_criteria": data.acceptance_criteria,
            "rollback_plan": data.rollback_plan,
            "payload": data.payload,
            "created_by": data.created_by,
        }
    )
    return {"id": task_id, "status": "success"}


@router.put(
    "/api/evolution/tasks/{task_id}",
    dependencies=[Depends(verify_token)],
    responses={400: {"description": "Task cannot edit"}, 404: {"description": TASK_NOT_FOUND}},
)
async def update_task(task_id: int, data: EvolutionTaskUpdateModel):
    task = db.get_evolution_task_by_id(task_id)
    block_reason = _validate_task_for_edit(task)
    if block_reason:
        raise HTTPException(status_code=404 if block_reason == TASK_NOT_FOUND else 400, detail=block_reason)

    updates: Dict[str, Any] = {}
    if data.title is not None:
        updates["title"] = data.title
    if data.description is not None:
        updates["description"] = data.description
    if data.source is not None:
        updates["source"] = data.source
    if data.task_type is not None:
        updates["task_type"] = data.task_type
    if data.scope is not None:
        updates["scope"] = data.scope
    if data.risk_level is not None:
        updates["risk_level"] = _normalize_risk_level(data.risk_level)
    if data.acceptance_criteria is not None:
        updates["acceptance_criteria"] = data.acceptance_criteria
    if data.rollback_plan is not None:
        updates["rollback_plan"] = data.rollback_plan
    if data.payload is not None:
        updates["payload"] = data.payload
    if data.max_retries is not None:
        updates["max_retries"] = max(1, int(data.max_retries))

    if not updates:
        return {"status": "success", "task": task}

    db.update_evolution_task(task_id, updates)
    return {"status": "success", "task": db.get_evolution_task_by_id(task_id)}


@router.post(
    "/api/evolution/tasks/{task_id}/cancel",
    dependencies=[Depends(verify_token)],
    responses={400: {"description": "Task cannot cancel"}, 404: {"description": TASK_NOT_FOUND}},
)
async def cancel_task(task_id: int, current_user: AuthUser):
    task = db.get_evolution_task_by_id(task_id)
    block_reason = _validate_task_for_cancel(task)
    if block_reason:
        raise HTTPException(status_code=404 if block_reason == TASK_NOT_FOUND else 400, detail=block_reason)
    operator = current_user.get("sub", "system")
    db.update_evolution_task(
        task_id,
        {
            "status": "cancelled",
            "needs_human_action": 0,
            "final_report": f"任务已被 {operator} 手动停用。",
        },
    )
    return {"status": "success", "task_status": "cancelled"}


@router.post(
    "/api/evolution/tasks/{task_id}/enable",
    dependencies=[Depends(verify_token)],
    responses={400: {"description": "Task cannot enable"}, 404: {"description": TASK_NOT_FOUND}},
)
async def enable_task(task_id: int):
    task = db.get_evolution_task_by_id(task_id)
    block_reason = _validate_task_for_enable(task)
    if block_reason:
        raise HTTPException(status_code=404 if block_reason == TASK_NOT_FOUND else 400, detail=block_reason)
    requires_approval = int(task.get("requires_approval", 0)) == 1
    approval_status = (task.get("approval_status") or "")
    next_status = "approved" if (not requires_approval or approval_status == "approved") else "pending_approval"
    db.update_evolution_task(
        task_id,
        {
            "status": next_status,
            "needs_human_action": 0,
            "final_report": None,
        },
    )
    return {"status": "success", "task_status": next_status}


@router.delete(
    "/api/evolution/tasks/{task_id}",
    dependencies=[Depends(verify_token)],
    responses={400: {"description": "Task cannot delete"}, 404: {"description": TASK_NOT_FOUND}},
)
async def delete_task(task_id: int):
    task = db.get_evolution_task_by_id(task_id)
    block_reason = _validate_task_for_delete(task)
    if block_reason:
        raise HTTPException(status_code=404 if block_reason == TASK_NOT_FOUND else 400, detail=block_reason)
    db.delete_evolution_task(task_id)
    return {"status": "success", "task_id": task_id}


@router.post(
    "/api/evolution/tasks/{task_id}/approve",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": TASK_NOT_FOUND}},
)
async def approve_task(task_id: int, current_user: AuthUser):
    task = db.get_evolution_task_by_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=TASK_NOT_FOUND)
    operator = current_user.get("sub", "system")
    now = datetime.now(timezone.utc).isoformat()
    db.update_evolution_task(
        task_id,
        {
            "approval_status": "approved",
            "status": "approved",
            "needs_human_action": 0,
            "approved_by": operator,
            "approved_at": now,
            "rejected_by": None,
            "rejected_at": None,
            "rejection_reason": None,
        },
    )
    return {"status": "success"}


@router.post(
    "/api/evolution/tasks/{task_id}/reject",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": TASK_NOT_FOUND}},
)
async def reject_task(
    task_id: int,
    current_user: AuthUser,
    data: Optional[EvolutionRejectModel] = None,
):
    task = db.get_evolution_task_by_id(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=TASK_NOT_FOUND)
    operator = current_user.get("sub", "system")
    now = datetime.now(timezone.utc).isoformat()
    db.update_evolution_task(
        task_id,
        {
            "approval_status": "rejected",
            "status": "blocked_manual",
            "needs_human_action": 1,
            "final_report": "审批拒绝，任务转人工处理。",
            "rejected_by": operator,
            "rejected_at": now,
            "rejection_reason": (data.reason if data else "") or "",
        },
    )
    return {"status": "success"}


@router.post(
    "/api/evolution/tasks/{task_id}/run",
    dependencies=[Depends(verify_token)],
    responses={400: {"description": "Task cannot run"}, 404: {"description": TASK_NOT_FOUND}},
)
async def run_task(task_id: int, run: EvolutionRunModel, current_user: AuthUser):
    task = db.get_evolution_task_by_id(task_id)
    block_reason = _validate_task_for_run(task)
    if block_reason:
        raise HTTPException(status_code=404 if block_reason == TASK_NOT_FOUND else 400, detail=block_reason)
    db.update_evolution_task(task_id, {"status": "running"})
    operator = current_user.get("sub", "system")
    is_success = (run.result or "success").lower() == "success"

    if is_success:
        db.update_evolution_task(
            task_id,
            {
                "status": "success",
                "error_signature": None,
                "error_repeat_count": 0,
                "needs_human_action": 0,
            },
        )
        db.add_evolution_run(
            task_id=task_id,
            run_status="success",
            trigger_type=run.trigger_type,
            detail=run.detail,
            result_json=run.result_payload,
            operator=operator,
        )
        return {"status": "success", "task_status": "success"}

    retry_count = int(task.get("retry_count", 0)) + 1
    max_retries = int(task.get("max_retries", 100))
    current_sig = (run.error_signature or "").strip() or None
    error_repeat_count = _calc_error_repeat_count(task, current_sig)

    blocked = retry_count >= max_retries
    new_status = "blocked_manual" if blocked else "failed"
    final_report = task.get("final_report")
    if blocked:
        final_report = (
            f"任务达到最大重试次数({max_retries})后自动停止。"
            "建议人工介入排查，并根据失败日志修订任务策略。"
        )

    db.update_evolution_task(
        task_id,
        {
            "status": new_status,
            "retry_count": retry_count,
            "error_signature": current_sig,
            "error_repeat_count": error_repeat_count,
            "needs_human_action": 1 if blocked else 0,
            "final_report": final_report,
        },
    )
    db.add_evolution_run(
        task_id=task_id,
        run_status="failed",
        trigger_type=run.trigger_type,
        detail=run.detail,
        result_json={"error_signature": current_sig, **(run.result_payload or {})},
        operator=operator,
    )
    return {
        "status": "success",
        "task_status": new_status,
        "retry_count": retry_count,
        "max_retries": max_retries,
    }


@router.post("/api/evolution/tasks/{task_id}/run_async", dependencies=[Depends(verify_token)])
async def run_task_async(task_id: int, current_user: AuthUser):
    operator = current_user.get("sub", "system")
    return _run_async_core(task_id=task_id, operator=operator, trigger_type="async")


@router.post(
    "/api/evolution/tasks/{task_id}/enqueue",
    dependencies=[Depends(verify_token)],
    responses={400: {"description": "Queue enqueue failed"}, 404: {"description": TASK_NOT_FOUND}},
)
async def enqueue_task(task_id: int):
    task = db.get_evolution_task_by_id(task_id)
    block_reason = _validate_task_for_run(task)
    if block_reason:
        raise HTTPException(status_code=404 if block_reason == TASK_NOT_FOUND else 400, detail=block_reason)
    if queue_manager is None:
        start_evolution_queue()
    assert queue_manager is not None
    try:
        queue_manager.enqueue(task_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Queue enqueue failed: {e}")
    db.update_evolution_task(task_id, {"status": "queued"})
    return {"status": "success", "task_status": "queued"}


@router.get("/api/evolution/queue/status", dependencies=[Depends(verify_token)])
async def get_queue_status():
    if queue_manager is None:
        return {
            "enabled": 0,
            "queued": 0,
            "running": 0,
            "workers": int(os.getenv("EVOLUTION_QUEUE_WORKERS", "1")),
            "processed": 0,
            "failed": 0,
            "max_size": int(os.getenv("EVOLUTION_QUEUE_MAX_SIZE", "100")),
        }
    return queue_manager.status()


@router.get("/api/evolution/scheduler/status", dependencies=[Depends(verify_token)])
async def get_scheduler_status():
    if scheduler_manager is None:
        return {
            "running": False,
            "last_result": {"picked": 0, "enqueued": 0, "skipped": 0, "enabled": False, "at": None},
            "config": db.get_evolution_scheduler_config(),
        }
    return scheduler_manager.status()


@router.post("/api/evolution/scheduler/config", dependencies=[Depends(verify_token)])
async def update_scheduler_config(data: EvolutionSchedulerConfigModel):
    db.update_evolution_scheduler_config(data.model_dump())
    return {"status": "success", "config": db.get_evolution_scheduler_config()}


@router.post("/api/evolution/scheduler/run_once", dependencies=[Depends(verify_token)])
async def run_scheduler_once():
    ret = _run_scheduler_once()
    return {"status": "success", **ret}


@router.get("/api/evolution/experiences", dependencies=[Depends(verify_token)])
async def list_experiences(limit: int = 50):
    return db.get_evolution_experiences(limit=limit)


@router.get("/api/evolution/failure_reports", dependencies=[Depends(verify_token)])
async def list_failure_reports(limit: int = 50):
    return db.get_evolution_failure_reports(limit=limit)


@router.get("/api/evolution/schema_migrations", dependencies=[Depends(verify_token)])
async def list_schema_migrations(limit: int = 50):
    return db.get_schema_migrations(limit=limit)


@router.post("/api/evolution/schema_migrations/apply", dependencies=[Depends(verify_token)])
async def apply_schema_migration(data: EvolutionMigrationApplyModel):
    task_id = db.add_evolution_task(
        {
            "title": f"DB迁移: {data.migration_name}",
            "description": "Phase D 数据库结构迁移任务",
            "source": "user",
            "task_type": "self_modify_db_schema",
            "scope": "db",
            "risk_level": "high",
            "status": "pending_approval",
            "requires_approval": 1,
            "approval_status": "pending",
            "max_retries": 100,
            "acceptance_criteria": "迁移执行成功并通过健康检查",
            "rollback_plan": "执行 down_sql 回滚迁移",
            "payload": {
                "commands": [],
                "verify_commands": [],
                "db_migration": {
                    "name": data.migration_name,
                    "up_sql": data.up_sql,
                    "down_sql": data.down_sql,
                },
            },
            "created_by": data.applied_by or "system",
        }
    )
    return {"status": "success", "task_id": task_id, "message": "已创建待审批迁移任务"}


@router.get("/api/evolution/templates/ops", dependencies=[Depends(verify_token)])
async def list_ops_templates():
    return _phase_d_ops_templates()


@router.post("/api/evolution/ai_uploads/init", dependencies=[Depends(verify_token)])
async def init_ai_upload(body: AIDialogUploadInitModel):
    with upload_state_lock:
        for existing in upload_states.values():
            if (
                existing.get("status") == "uploading"
                and existing.get("session_id") == body.session_id
                and existing.get("file_name") == _sanitize_file_name(body.file_name)
                and int(existing.get("file_size", 0)) == int(body.file_size)
                and int(existing.get("chunk_size", 0)) == int(body.chunk_size)
            ):
                return {"status": "success", **_state_brief(existing)}
    upload_id = f"upl_{int(datetime.now(timezone.utc).timestamp())}_{os.urandom(4).hex()}"
    safe_name = _sanitize_file_name(body.file_name)
    total_chunks = max(1, (int(body.file_size) + int(body.chunk_size) - 1) // int(body.chunk_size))
    state = {
        "upload_id": upload_id,
        "session_id": body.session_id,
        "file_name": safe_name,
        "file_size": int(body.file_size),
        "chunk_size": int(body.chunk_size),
        "total_chunks": total_chunks,
        "uploaded_chunks": set(),
        "uploaded_bytes": 0,
        "status": "uploading",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mime_type": body.mime_type or "",
    }
    with upload_state_lock:
        upload_states[upload_id] = state
    return {"status": "success", **_state_brief(state)}


@router.post(
    "/api/evolution/ai_uploads/{upload_id}/chunk",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": UPLOAD_NOT_FOUND}, 400: {"description": "Upload state invalid"}},
)
async def upload_ai_chunk(
    upload_id: str,
    chunk_index: Annotated[int, Form(...)],
    file: Annotated[UploadFile, File(...)],
):
    with upload_state_lock:
        state = upload_states.get(upload_id)
    if not state:
        raise HTTPException(status_code=404, detail=UPLOAD_NOT_FOUND)
    if state.get("status") != "uploading":
        raise HTTPException(status_code=400, detail="Upload already completed or cancelled")
    total_chunks = int(state["total_chunks"])
    if chunk_index < 0 or chunk_index >= total_chunks:
        raise HTTPException(status_code=400, detail="chunk_index out of range")
    data = await file.read()
    chunk_dir = _upload_dir(upload_id)
    chunk_dir.mkdir(parents=True, exist_ok=True)
    chunk_path = chunk_dir / f"{chunk_index:08d}.part"
    if not chunk_path.exists():
        chunk_path.write_bytes(data)
        with upload_state_lock:
            state = upload_states.get(upload_id)
            if state:
                uploaded = state.setdefault("uploaded_chunks", set())
                uploaded.add(int(chunk_index))
                state["uploaded_bytes"] = int(state.get("uploaded_bytes", 0)) + len(data)
    with upload_state_lock:
        state = upload_states.get(upload_id)
        if not state:
            raise HTTPException(status_code=404, detail=UPLOAD_NOT_FOUND)
        brief = _state_brief(state)
    return {"status": "success", **brief}


@router.get(
    "/api/evolution/ai_uploads/{upload_id}/progress",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": UPLOAD_NOT_FOUND}},
)
async def get_ai_upload_progress(upload_id: str):
    with upload_state_lock:
        state = upload_states.get(upload_id)
    if not state:
        raise HTTPException(status_code=404, detail=UPLOAD_NOT_FOUND)
    return {"status": "success", **_state_brief(state)}


@router.get(
    "/api/evolution/ai_uploads/{upload_id}/chunks",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": UPLOAD_NOT_FOUND}},
)
async def get_ai_upload_chunks(upload_id: str):
    with upload_state_lock:
        state = upload_states.get(upload_id)
    if not state:
        raise HTTPException(status_code=404, detail=UPLOAD_NOT_FOUND)
    return {"status": "success", "upload_id": upload_id, "uploaded_chunk_indexes": _uploaded_chunk_indexes(state)}


@router.post(
    "/api/evolution/ai_uploads/{upload_id}/complete",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": UPLOAD_NOT_FOUND}, 400: {"description": "Chunks missing"}},
)
async def complete_ai_upload(upload_id: str, session_id: Annotated[str, Form(...)]):
    with upload_state_lock:
        state = upload_states.get(upload_id)
    if not state:
        raise HTTPException(status_code=404, detail=UPLOAD_NOT_FOUND)
    if state.get("session_id") != session_id:
        raise HTTPException(status_code=400, detail="session_id mismatch")
    total_chunks = int(state["total_chunks"])
    uploaded_chunks = state.get("uploaded_chunks", set())
    if len(uploaded_chunks) < total_chunks:
        raise HTTPException(status_code=400, detail="Chunks missing")

    chunk_dir = _upload_dir(upload_id)
    output_name = state.get("file_name", "upload.bin")
    output_path = chunk_dir / output_name
    with output_path.open("wb") as out:
        for idx in range(total_chunks):
            part_path = chunk_dir / f"{idx:08d}.part"
            if not part_path.exists():
                raise HTTPException(status_code=400, detail=f"Missing chunk: {idx}")
            out.write(part_path.read_bytes())

    with upload_state_lock:
        state = upload_states.get(upload_id)
        if not state:
            raise HTTPException(status_code=404, detail=UPLOAD_NOT_FOUND)
        state["status"] = "completed"
        state["final_path"] = str(output_path)
        state["completed_at"] = datetime.now(timezone.utc).isoformat()
        brief = _state_brief(state)
    return {"status": "success", **brief, "final_path": str(output_path)}


@router.get("/api/evolution/plugins", dependencies=[Depends(verify_token)])
async def list_plugins(status: Optional[str] = None):
    return db.get_all_plugins(status=status)


@router.get(
    "/api/evolution/plugins/{plugin_id}",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": PLUGIN_NOT_FOUND}},
)
async def get_plugin(plugin_id: str):
    plugin = db.get_plugin_by_plugin_id(plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail=PLUGIN_NOT_FOUND)
    plugin["schedules"] = db.get_schedules_by_plugin(plugin_id)
    return plugin


@router.post(
    "/api/evolution/plugins/install",
    dependencies=[Depends(verify_token)],
    responses={400: {"description": "Invalid plugin manifest"}},
)
async def install_plugin(data: PluginInstallModel):
    try:
        installed = plugin_manager.install_plugin(data.manifest, data.files)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.upsert_plugin_registry(
        {
            "plugin_id": installed["plugin_id"],
            "name": installed["name"],
            "version": installed["version"],
            "status": "installed",
            "runtime": installed["runtime"],
            "manifest": installed["manifest"],
            "install_path": installed["install_path"],
        }
    )
    db.replace_plugin_schedules(installed["plugin_id"], installed["manifest"].get("schedules") or [])
    return {"status": "success", "plugin_id": installed["plugin_id"]}


@router.post(
    "/api/evolution/plugins/{plugin_id}/enable",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": PLUGIN_NOT_FOUND}},
)
async def enable_plugin(plugin_id: str):
    plugin = db.get_plugin_by_plugin_id(plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail=PLUGIN_NOT_FOUND)
    try:
        manifest = plugin_manager.set_enabled(plugin_id, True)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    db.upsert_plugin_registry(
        {
            "plugin_id": plugin_id,
            "name": manifest.get("name") or plugin_id,
            "version": manifest.get("version") or "0.1.0",
            "status": "enabled",
            "runtime": manifest.get("runtime") or "python",
            "manifest": manifest,
            "install_path": plugin.get("install_path"),
        }
    )
    return {"status": "success", "plugin_id": plugin_id, "plugin_status": "enabled"}


@router.post(
    "/api/evolution/plugins/{plugin_id}/disable",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": PLUGIN_NOT_FOUND}},
)
async def disable_plugin(plugin_id: str):
    plugin = db.get_plugin_by_plugin_id(plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail=PLUGIN_NOT_FOUND)
    try:
        manifest = plugin_manager.set_enabled(plugin_id, False)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    db.upsert_plugin_registry(
        {
            "plugin_id": plugin_id,
            "name": manifest.get("name") or plugin_id,
            "version": manifest.get("version") or "0.1.0",
            "status": "disabled",
            "runtime": manifest.get("runtime") or "python",
            "manifest": manifest,
            "install_path": plugin.get("install_path"),
        }
    )
    return {"status": "success", "plugin_id": plugin_id, "plugin_status": "disabled"}


@router.delete(
    "/api/evolution/plugins/{plugin_id}",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": PLUGIN_NOT_FOUND}},
)
async def uninstall_plugin(plugin_id: str):
    plugin = db.get_plugin_by_plugin_id(plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail=PLUGIN_NOT_FOUND)
    archive_path = plugin_manager.uninstall_plugin(plugin_id)
    db.delete_plugin_registry(plugin_id)
    db.replace_plugin_schedules(plugin_id, [])
    return {"status": "success", "plugin_id": plugin_id, "archive_path": archive_path}


@router.post(
    "/api/evolution/plugins/{plugin_id}/submit_task",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": "Plugin or task template not found"}},
)
async def submit_plugin_template_task(plugin_id: str, body: PluginTemplateTaskModel):
    plugin = db.get_plugin_by_plugin_id(plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail=PLUGIN_NOT_FOUND)
    manifest = plugin.get("manifest") or {}
    templates = manifest.get("task_templates") or []
    target = _find_task_template(templates, body.template_key)
    if not target:
        raise HTTPException(status_code=404, detail=TASK_TEMPLATE_NOT_FOUND)
    task_data = _build_plugin_task_data(
        plugin_id=plugin_id,
        template=target,
        created_by=body.created_by or "plugin",
        overrides=body.overrides or {},
    )
    task_id = db.add_evolution_task(task_data)
    return {"status": "success", "task_id": task_id}


@router.post("/api/evolution/demo/seed", dependencies=[Depends(verify_token)])
async def seed_demo_data(
    current_user: AuthUser,
    body: Optional[EvolutionDemoSeedModel] = None,
):
    if body and body.clear_existing:
        db.clear_evolution_data()

    operator = current_user.get("sub", "system")
    demo_tasks = _demo_seed_tasks(operator)

    created_ids = []
    for item in demo_tasks:
        tid = db.add_evolution_task(item)
        created_ids.append(tid)

    # 生成 2 条失败日志
    if len(created_ids) >= 5:
        db.add_evolution_run(
            task_id=created_ids[3],
            run_status="failed",
            trigger_type="manual",
            detail="示例失败日志：数据库迁移锁冲突",
            result_json={"error_signature": "migration_lock_timeout", "code": "DB_LOCK"},
            operator=operator,
        )
        db.add_evolution_run(
            task_id=created_ids[4],
            run_status="failed",
            trigger_type="manual",
            detail="示例失败日志：CSS 编译失败",
            result_json={"error_signature": "css_build_failed", "code": "CSS_BUILD"},
            operator=operator,
        )

    return {
        "status": "success",
        "created_tasks": len(created_ids),
        "created_failed_runs": 2,
        "task_ids": created_ids,
    }


@router.post("/api/evolution/demo/reset", dependencies=[Depends(verify_token)])
async def reset_demo_data():
    db.clear_evolution_data()
    return {"status": "success", "message": "演示数据已清空"}

