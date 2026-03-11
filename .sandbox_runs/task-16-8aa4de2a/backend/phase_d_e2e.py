import json
import os
from typing import Any, Dict

import requests


BASE_URL = os.getenv("E2E_BASE_URL", "http://127.0.0.1:9000").rstrip("/")
PASSWORD = os.getenv("E2E_PASSWORD", os.getenv("APP_PASSWORD", "admin"))
TIMEOUT = 30


def req(method: str, path: str, token: str = "", **kwargs):
    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return requests.request(method, f"{BASE_URL}{path}", headers=headers, timeout=TIMEOUT, **kwargs)


def must_ok(resp: requests.Response, step: str):
    if resp.status_code >= 400:
        raise RuntimeError(f"{step} 失败: {resp.status_code} {resp.text}")
    return resp.json()


def approve_if_needed(task_id: int, token: str):
    task = must_ok(req("GET", f"/api/evolution/tasks/{task_id}", token=token), "获取任务详情")
    if (task.get("approval_status") or "") == "pending":
        must_ok(req("POST", f"/api/evolution/tasks/{task_id}/approve", token=token), "审批任务")


def create_task(body: Dict[str, Any], token: str) -> int:
    ret = must_ok(req("POST", "/api/evolution/tasks", token=token, json=body), "创建任务")
    return int(ret["id"])


def run_async(task_id: int, token: str) -> Dict[str, Any]:
    return must_ok(req("POST", f"/api/evolution/tasks/{task_id}/run_async", token=token), "异步执行任务")


def main():
    # 1) 登录
    login = must_ok(req("POST", "/api/login", json={"password": PASSWORD}), "登录")
    token = login["access_token"]

    # 2) 拉取模板并按模板创建任务
    templates = must_ok(req("GET", "/api/evolution/templates/ops", token=token), "拉取运维模板")
    if not templates:
        raise RuntimeError("运维模板为空，无法执行 E2E")
    tmpl_task = templates[0]["task"]
    tmpl_task["title"] = f"{tmpl_task.get('title', '模板任务')} [E2E]"
    template_task_id = create_task(tmpl_task, token)
    approve_if_needed(template_task_id, token)
    template_run = run_async(template_task_id, token)

    # 3) 构造失败任务，触发无法修复报告（max_retries=1）
    fail_task_id = create_task(
        {
            "title": "E2E 无法修复报告验证",
            "description": "执行失败并触发阻断报告",
            "source": "user",
            "task_type": "fix",
            "scope": "backend",
            "risk_level": "low",
            "max_retries": 1,
            "payload": {
                "commands": ['python -c "import sys; sys.exit(1)"'],
                "verify_commands": [],
                "hot_reload_commands": [],
                "health_check_commands": [],
                "rollback_commands": [],
            },
        },
        token,
    )
    approve_if_needed(fail_task_id, token)
    fail_run = run_async(fail_task_id, token)

    # 4) 构造迁移任务并强制回滚（健康检查失败）
    migration_name = "e2e_create_tmp_table"
    migration_task_id = create_task(
        {
            "title": "E2E 迁移回滚验证",
            "description": "执行 up_sql 后在健康检查失败时自动 down_sql 回滚",
            "source": "user",
            "task_type": "self_modify_db_schema",
            "scope": "db",
            "risk_level": "high",
            "max_retries": 1,
            "payload": {
                "commands": [],
                "verify_commands": [],
                "hot_reload_commands": [],
                "health_check_commands": ['python -c "import sys; sys.exit(1)"'],
                "rollback_commands": ["echo migration rollback hook"],
                "db_migration": {
                    "name": migration_name,
                    "up_sql": "CREATE TABLE IF NOT EXISTS e2e_tmp_table(id INTEGER PRIMARY KEY);",
                    "down_sql": "DROP TABLE IF EXISTS e2e_tmp_table;",
                },
            },
        },
        token,
    )
    approve_if_needed(migration_task_id, token)
    migration_run = run_async(migration_task_id, token)

    # 5) 拉取 Phase D 产物
    experiences = must_ok(req("GET", "/api/evolution/experiences?limit=20", token=token), "获取经验库")
    reports = must_ok(req("GET", "/api/evolution/failure_reports?limit=20", token=token), "获取失败报告")
    migrations = must_ok(req("GET", "/api/evolution/schema_migrations?limit=20", token=token), "获取迁移审计")

    has_fail_report = any(int(x.get("task_id", 0)) == fail_task_id for x in reports)
    has_experience = any(int(x.get("task_id", 0)) in (fail_task_id, migration_task_id) for x in experiences)
    has_rollback_migration = any((x.get("migration_name") == migration_name and x.get("status") in ("rolled_back", "failed")) for x in migrations)

    summary = {
        "base_url": BASE_URL,
        "template_task_id": template_task_id,
        "template_task_status": template_run.get("task_status"),
        "fail_task_id": fail_task_id,
        "fail_task_status": fail_run.get("task_status"),
        "fail_report_id": fail_run.get("report_id"),
        "migration_task_id": migration_task_id,
        "migration_task_status": migration_run.get("task_status"),
        "checks": {
            "has_fail_report": has_fail_report,
            "has_experience": has_experience,
            "has_rollback_migration": has_rollback_migration,
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if not all(summary["checks"].values()):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
