import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from statistics import mean
from typing import Any, Dict, List

import requests


BASE_URL = os.getenv("STRESS_BASE_URL", "http://127.0.0.1:9000").rstrip("/")
PASSWORD = os.getenv("STRESS_PASSWORD", os.getenv("APP_PASSWORD", "admin"))
TIMEOUT = 30
SUCCESS_CONCURRENCY = int(os.getenv("STRESS_SUCCESS_CONCURRENCY", "8"))
FAIL_CONCURRENCY = int(os.getenv("STRESS_FAIL_CONCURRENCY", "5"))
MIGRATION_CONCURRENCY = int(os.getenv("STRESS_MIGRATION_CONCURRENCY", "3"))
RETRY_TIMES = int(os.getenv("STRESS_RETRY_TIMES", "4"))


def req(method: str, path: str, token: str = "", **kwargs) -> requests.Response:
    headers = kwargs.pop("headers", {})
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return requests.request(method, f"{BASE_URL}{path}", headers=headers, timeout=TIMEOUT, **kwargs)


def _is_retryable(resp: requests.Response) -> bool:
    body = ""
    try:
        body = (resp.text or "").lower()
    except Exception:
        body = ""
    if resp.status_code in (429, 500, 502, 503, 504):
        return True
    if resp.status_code == 400 and ("locked" in body or "timeout" in body):
        return True
    return False


def call_json(method: str, path: str, step: str, token: str = "", **kwargs) -> Dict[str, Any]:
    last_error = ""
    for attempt in range(1, RETRY_TIMES + 1):
        try:
            resp = req(method, path, token=token, **kwargs)
            if resp.status_code < 400:
                return resp.json()
            if _is_retryable(resp) and attempt < RETRY_TIMES:
                time.sleep(0.4 * attempt)
                continue
            last_error = f"{resp.status_code} {resp.text}"
            break
        except Exception as e:
            last_error = str(e)
            if attempt < RETRY_TIMES:
                time.sleep(0.4 * attempt)
                continue
            break
    raise RuntimeError(f"{step} 失败: {last_error}")


def create_task(body: Dict[str, Any], token: str) -> int:
    ret = call_json("POST", "/api/evolution/tasks", "创建任务", token=token, json=body)
    return int(ret["id"])


def approve_if_needed(task_id: int, token: str):
    task = call_json("GET", f"/api/evolution/tasks/{task_id}", "获取任务详情", token=token)
    if (task.get("approval_status") or "") == "pending":
        call_json("POST", f"/api/evolution/tasks/{task_id}/approve", "审批任务", token=token)


def run_async(task_id: int, token: str) -> Dict[str, Any]:
    return call_json("POST", f"/api/evolution/tasks/{task_id}/run_async", "异步执行任务", token=token)


def fetch_count(path: str, token: str) -> int:
    data = call_json("GET", path, f"获取 {path}", token=token)
    return len(data) if isinstance(data, list) else 0


def success_case(idx: int, token: str) -> Dict[str, Any]:
    payload = {
        "title": f"STRESS 成功任务 #{idx}",
        "description": "并发成功用例",
        "source": "user",
        "task_type": "feature",
        "scope": "backend",
        "risk_level": "low",
        "max_retries": 2,
        "payload": {
            "commands": ['python -c "print(\'ok\')"'],
            "verify_commands": ['python -c "print(\'verify\')"'],
            "hot_reload_commands": [],
            "health_check_commands": [],
            "rollback_commands": [],
        },
    }
    task_id = create_task(payload, token)
    approve_if_needed(task_id, token)
    start = time.time()
    ret = run_async(task_id, token)
    elapsed_ms = int((time.time() - start) * 1000)
    return {"task_id": task_id, "task_status": ret.get("task_status"), "elapsed_ms": elapsed_ms}


def failure_case(idx: int, token: str) -> Dict[str, Any]:
    payload = {
        "title": f"STRESS 失败任务 #{idx}",
        "description": "并发失败注入，触发阻断报告",
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
    }
    task_id = create_task(payload, token)
    approve_if_needed(task_id, token)
    start = time.time()
    ret = run_async(task_id, token)
    elapsed_ms = int((time.time() - start) * 1000)
    return {
        "task_id": task_id,
        "task_status": ret.get("task_status"),
        "report_id": ret.get("report_id"),
        "elapsed_ms": elapsed_ms,
    }


def migration_case(idx: int, token: str) -> Dict[str, Any]:
    # 偶数故意触发健康检查失败，验证回滚链路
    should_fail_health = (idx % 2 == 0)
    migration_name = f"stress_migration_{idx}_{int(time.time() * 1000)}"
    payload = {
        "title": f"STRESS 迁移任务 #{idx}",
        "description": "并发迁移与回滚注入",
        "source": "user",
        "task_type": "self_modify_db_schema",
        "scope": "db",
        "risk_level": "high",
        "max_retries": 1,
        "payload": {
            "commands": [],
            "verify_commands": [],
            "hot_reload_commands": [],
            "health_check_commands": ['python -c "import sys; sys.exit(1)"'] if should_fail_health else [],
            "rollback_commands": ["echo rollback migration"],
            "db_migration": {
                "name": migration_name,
                "up_sql": f"CREATE TABLE IF NOT EXISTS {migration_name}(id INTEGER PRIMARY KEY);",
                "down_sql": f"DROP TABLE IF EXISTS {migration_name};",
            },
        },
    }
    task_id = create_task(payload, token)
    approve_if_needed(task_id, token)
    start = time.time()
    ret = run_async(task_id, token)
    elapsed_ms = int((time.time() - start) * 1000)
    return {
        "task_id": task_id,
        "migration_name": migration_name,
        "task_status": ret.get("task_status"),
        "elapsed_ms": elapsed_ms,
    }


def run_pool(label: str, size: int, fn, token: str) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(size, 16))) as executor:
        futures = [executor.submit(fn, idx, token) for idx in range(size)]
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                results.append({"task_status": "exception", "error": str(e), "label": label})
    return results


def p95(nums: List[int]) -> int:
    if not nums:
        return 0
    arr = sorted(nums)
    idx = int(round((len(arr) - 1) * 0.95))
    return int(arr[idx])


def main():
    login = call_json("POST", "/api/login", "登录", json={"password": PASSWORD})
    token = login["access_token"]

    before_reports = fetch_count("/api/evolution/failure_reports?limit=200", token)
    before_exp = fetch_count("/api/evolution/experiences?limit=200", token)
    before_mig = fetch_count("/api/evolution/schema_migrations?limit=200", token)

    started_at = time.time()
    success_results = run_pool("success", SUCCESS_CONCURRENCY, success_case, token)
    fail_results = run_pool("failure", FAIL_CONCURRENCY, failure_case, token)
    migration_results = run_pool("migration", MIGRATION_CONCURRENCY, migration_case, token)
    total_elapsed_ms = int((time.time() - started_at) * 1000)

    after_reports = fetch_count("/api/evolution/failure_reports?limit=200", token)
    after_exp = fetch_count("/api/evolution/experiences?limit=200", token)
    after_mig = fetch_count("/api/evolution/schema_migrations?limit=200", token)

    all_results = success_results + fail_results + migration_results
    elapsed_values = [int(x.get("elapsed_ms", 0)) for x in all_results if x.get("elapsed_ms") is not None]

    summary = {
        "base_url": BASE_URL,
        "concurrency": {
            "success": SUCCESS_CONCURRENCY,
            "failure": FAIL_CONCURRENCY,
            "migration": MIGRATION_CONCURRENCY,
        },
        "duration_ms": total_elapsed_ms,
        "result_counts": {
            "success": sum(1 for x in all_results if x.get("task_status") == "success"),
            "blocked_manual": sum(1 for x in all_results if x.get("task_status") == "blocked_manual"),
            "rolled_back": sum(1 for x in all_results if x.get("task_status") == "rolled_back"),
            "failed": sum(1 for x in all_results if x.get("task_status") == "failed"),
            "exception": sum(1 for x in all_results if x.get("task_status") == "exception"),
        },
        "latency_ms": {
            "avg": int(mean(elapsed_values)) if elapsed_values else 0,
            "p95": p95(elapsed_values),
            "max": max(elapsed_values) if elapsed_values else 0,
        },
        "deltas": {
            "failure_reports": after_reports - before_reports,
            "experiences": after_exp - before_exp,
            "schema_migrations": after_mig - before_mig,
        },
        "checks": {
            "no_exceptions": all(x.get("task_status") != "exception" for x in all_results),
            "failure_reports_increased": (after_reports - before_reports) >= 1,
            "experiences_increased": (after_exp - before_exp) >= 1,
            "schema_migrations_increased": (after_mig - before_mig) >= 1,
            "has_rolled_back_or_failed_migration": any(
                x.get("task_status") in ("rolled_back", "failed") for x in migration_results
            ),
        },
        "exceptions": [x for x in all_results if x.get("task_status") == "exception"][:10],
    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if not all(summary["checks"].values()):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
