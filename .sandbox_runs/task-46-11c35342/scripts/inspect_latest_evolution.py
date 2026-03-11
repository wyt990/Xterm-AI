import json
import sqlite3

DB = "d:/AI/xterm/config/xterm.db"


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    c.execute("PRAGMA table_info(evolution_tasks)")
    task_cols = [r["name"] for r in c.fetchall()]
    print("task_cols:", task_cols)

    c.execute("PRAGMA table_info(evolution_runs)")
    run_cols = [r["name"] for r in c.fetchall()]
    print("run_cols:", run_cols)

    c.execute(
        "select id,title,status,approval_status,source,task_type,scope,error_signature,retry_count,max_retries,created_at,last_run_at,payload_json from evolution_tasks order by id desc limit 12"
    )
    tasks = c.fetchall()
    print("tasks:", len(tasks))
    for r in tasks:
        d = dict(r)
        payload = d.get("payload_json") or "{}"
        try:
            pj = json.loads(payload)
        except Exception:
            pj = {}
        print(
            {
                "id": d["id"],
                "title": d["title"],
                "status": d["status"],
                "approval": d["approval_status"],
                "error_signature": d.get("error_signature"),
                "retry": f'{d.get("retry_count")}/{d.get("max_retries")}',
                "type": d["task_type"],
                "scope": d["scope"],
                "commands_len": len(pj.get("commands") or []),
                "verify_len": len(pj.get("verify_commands") or []),
                "allow_write_paths": pj.get("allow_write_paths"),
                "last_run_at": d.get("last_run_at"),
            }
        )

    c.execute(
        "select id,task_id,run_status,trigger_type,operator,detail,result_json,started_at,finished_at from evolution_runs order by id desc limit 20"
    )
    runs = c.fetchall()
    print("runs:", len(runs))
    for r in runs:
        d = dict(r)
        try:
            result = json.loads(d.get("result_json") or "{}")
        except Exception:
            result = {}
        logs = result.get("logs") or []
        print(
            {
                "run_id": d["id"],
                "task_id": d["task_id"],
                "run_status": d["run_status"],
                "trigger": d["trigger_type"],
                "operator": d["operator"],
                "error_signature": result.get("error_signature"),
                "changed_files": result.get("changed_files"),
                "stages": [x.get("stage") for x in logs],
            }
        )
        for lg in logs[-3:]:
            print(
                "  log",
                {
                    "stage": lg.get("stage"),
                    "ok": lg.get("ok"),
                    "exit_code": lg.get("exit_code"),
                    "command": str(lg.get("command") or "")[:120],
                    "stderr": str(lg.get("stderr") or "")[:160],
                },
            )

    conn.close()


if __name__ == "__main__":
    main()
