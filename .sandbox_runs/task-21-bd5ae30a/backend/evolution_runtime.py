import json
import os
import shutil
import sqlite3
import subprocess
import time
import uuid
from typing import Any, Dict, List, Tuple


def _repo_root() -> str:
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def _sandbox_root() -> str:
    root = os.path.join(_repo_root(), ".sandbox_runs")
    os.makedirs(root, exist_ok=True)
    return root


def _ignore_dirs(_src: str, names: List[str]):
    ignored = set()
    for n in names:
        if n in {".git", "node_modules", "dist", "build", "__pycache__", ".sandbox_runs", ".cursor", ".agents"}:
            ignored.add(n)
    return ignored


class EvolutionRuntime:
    def __init__(self):
        self.repo_root = _repo_root()
        self.sandbox_root = _sandbox_root()

    def create_sandbox(self, task_id: int) -> str:
        run_id = f"task-{task_id}-{uuid.uuid4().hex[:8]}"
        target = os.path.join(self.sandbox_root, run_id)
        shutil.copytree(self.repo_root, target, ignore=_ignore_dirs)
        return target

    def _run_command(self, command: str, cwd: str, timeout_sec: int = 300) -> Dict[str, Any]:
        start = time.time()
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            elapsed = int((time.time() - start) * 1000)
            return {
                "command": command,
                "exit_code": int(proc.returncode),
                "ok": proc.returncode == 0,
                "stdout": proc.stdout or "",
                "stderr": proc.stderr or "",
                "elapsed_ms": elapsed,
            }
        except subprocess.TimeoutExpired:
            elapsed = int((time.time() - start) * 1000)
            return {
                "command": command,
                "exit_code": -1,
                "ok": False,
                "stdout": "",
                "stderr": f"Timeout after {timeout_sec}s",
                "elapsed_ms": elapsed,
            }
        except Exception as e:
            elapsed = int((time.time() - start) * 1000)
            return {
                "command": command,
                "exit_code": -2,
                "ok": False,
                "stdout": "",
                "stderr": str(e),
                "elapsed_ms": elapsed,
            }

    def _run_stage(self, stage_name: str, commands: List[str], cwd: str, timeout_sec: int) -> Tuple[bool, List[Dict[str, Any]]]:
        logs: List[Dict[str, Any]] = []
        all_ok = True
        for cmd in commands:
            ret = self._run_command(cmd, cwd=cwd, timeout_sec=timeout_sec)
            logs.append({"stage": stage_name, **ret})
            if not ret["ok"]:
                all_ok = False
                break
        return all_ok, logs

    def _copy_back_changes(
        self,
        sandbox_path: str,
        allow_paths: List[str],
    ) -> Tuple[List[str], Dict[str, Dict[str, Any]]]:
        changed_files: List[str] = []
        backups: Dict[str, Dict[str, Any]] = {}
        for allow in allow_paths:
            normalized = allow.replace("\\", "/").strip("/")
            if not normalized:
                continue
            src_root = os.path.join(sandbox_path, normalized)
            if not os.path.exists(src_root):
                continue
            for root, _dirs, files in os.walk(src_root):
                for filename in files:
                    src_file = os.path.join(root, filename)
                    rel_path = os.path.relpath(src_file, sandbox_path)
                    dst_file = os.path.join(self.repo_root, rel_path)
                    os.makedirs(os.path.dirname(dst_file), exist_ok=True)
                    before = None
                    existed = os.path.exists(dst_file)
                    if existed:
                        with open(dst_file, "rb") as f:
                            before = f.read()
                    with open(src_file, "rb") as f:
                        after = f.read()
                    if existed and before == after:
                        continue
                    backups[rel_path] = {"existed": existed, "content": before}
                    with open(dst_file, "wb") as f:
                        f.write(after)
                    changed_files.append(rel_path.replace("\\", "/"))
        return changed_files, backups

    def _restore_files(self, backups: Dict[str, Dict[str, Any]]) -> List[str]:
        restored: List[str] = []
        for rel_path, item in backups.items():
            target = os.path.join(self.repo_root, rel_path)
            existed = bool(item.get("existed"))
            if existed:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "wb") as f:
                    f.write(item.get("content") or b"")
            elif os.path.exists(target):
                os.remove(target)
            restored.append(rel_path.replace("\\", "/"))
        return restored

    def _apply_db_migration(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        migration = payload.get("db_migration")
        if not migration:
            return {"enabled": False}
        db_path = migration.get("db_path") or os.path.join(self.repo_root, "config", "xterm.db")
        up_sql = migration.get("up_sql") or ""
        down_sql = migration.get("down_sql") or ""
        name = migration.get("name") or f"migration-{int(time.time())}"
        if not up_sql.strip():
            return {"enabled": True, "ok": False, "error": "up_sql is empty", "name": name}

        conn = sqlite3.connect(db_path)
        try:
            conn.execute("BEGIN")
            conn.executescript(up_sql)
            conn.commit()
            return {
                "enabled": True,
                "ok": True,
                "name": name,
                "db_path": db_path,
                "up_sql": up_sql,
                "down_sql": down_sql,
            }
        except Exception as e:
            conn.rollback()
            return {
                "enabled": True,
                "ok": False,
                "name": name,
                "db_path": db_path,
                "up_sql": up_sql,
                "down_sql": down_sql,
                "error": str(e),
            }
        finally:
            conn.close()

    def _rollback_db_migration(self, migration_result: Dict[str, Any]) -> Dict[str, Any]:
        if not migration_result.get("enabled") or not migration_result.get("ok"):
            return {"attempted": False}
        down_sql = migration_result.get("down_sql") or ""
        if not down_sql.strip():
            return {"attempted": True, "ok": False, "error": "down_sql is empty"}
        db_path = migration_result.get("db_path")
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("BEGIN")
            conn.executescript(down_sql)
            conn.commit()
            return {"attempted": True, "ok": True}
        except Exception as e:
            conn.rollback()
            return {"attempted": True, "ok": False, "error": str(e)}
        finally:
            conn.close()

    def execute_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        payload = task.get("payload") or {}
        timeout_sec = int(payload.get("timeout_sec", 300))
        sandbox_path = self.create_sandbox(task["id"])
        stage_logs: List[Dict[str, Any]] = []
        changed_files: List[str] = []
        file_backups: Dict[str, Dict[str, Any]] = {}

        # 1) primary commands
        commands = payload.get("commands") or []
        ok, logs = self._run_stage("commands", commands, sandbox_path, timeout_sec)
        stage_logs.extend(logs)
        if not ok:
            return {
                "ok": False,
                "task_status": "failed",
                "sandbox_path": sandbox_path,
                "logs": stage_logs,
                "error_signature": "commands_failed",
            }

        # 2) validation
        verify_commands = payload.get("verify_commands") or []
        ok, logs = self._run_stage("verify", verify_commands, sandbox_path, timeout_sec)
        stage_logs.extend(logs)
        if not ok:
            return {
                "ok": False,
                "task_status": "failed",
                "sandbox_path": sandbox_path,
                "logs": stage_logs,
                "error_signature": "verify_failed",
            }

        # 3) apply migration for db schema evolution
        migration_result: Dict[str, Any] = self._apply_db_migration(payload)
        if migration_result.get("enabled"):
            stage_logs.append(
                {
                    "stage": "db_migration",
                    "command": migration_result.get("name"),
                    "ok": bool(migration_result.get("ok", False)),
                    "exit_code": 0 if migration_result.get("ok") else 1,
                    "stdout": "migration applied" if migration_result.get("ok") else "",
                    "stderr": migration_result.get("error", ""),
                    "elapsed_ms": 0,
                }
            )
            if not migration_result.get("ok"):
                return {
                    "ok": False,
                    "task_status": "failed",
                    "sandbox_path": sandbox_path,
                    "logs": stage_logs,
                    "error_signature": "db_migration_failed",
                    "migration": migration_result,
                }

        # 4) copy back code changes for hot reload bridge
        allow_paths = payload.get("allow_write_paths") or ["frontend", "backend", "plugins", "docs"]
        changed_files, file_backups = self._copy_back_changes(sandbox_path=sandbox_path, allow_paths=allow_paths)
        stage_logs.append(
            {
                "stage": "sync_to_repo",
                "command": "sandbox->repo",
                "ok": True,
                "exit_code": 0,
                "stdout": f"changed_files={len(changed_files)}",
                "stderr": "",
                "elapsed_ms": 0,
            }
        )

        # 5) hot reload trigger
        hot_reload_commands = payload.get("hot_reload_commands") or []
        if hot_reload_commands:
            ok, logs = self._run_stage("hot_reload", hot_reload_commands, self.repo_root, timeout_sec)
            stage_logs.extend(logs)
            if not ok:
                restored = self._restore_files(file_backups)
                stage_logs.append(
                    {
                        "stage": "rollback_files",
                        "command": "restore_files_after_hot_reload_failed",
                        "ok": True,
                        "exit_code": 0,
                        "stdout": f"restored={len(restored)}",
                        "stderr": "",
                        "elapsed_ms": 0,
                    }
                )
                migration_rb = self._rollback_db_migration(migration_result)
                stage_logs.append(
                    {
                        "stage": "rollback_migration",
                        "command": "down_sql",
                        "ok": bool(migration_rb.get("ok", False) or not migration_rb.get("attempted")),
                        "exit_code": 0 if (migration_rb.get("ok", False) or not migration_rb.get("attempted")) else 1,
                        "stdout": "migration rolled back" if migration_rb.get("ok") else "",
                        "stderr": migration_rb.get("error", ""),
                        "elapsed_ms": 0,
                    }
                )
                return {
                    "ok": False,
                    "task_status": "rolled_back",
                    "sandbox_path": sandbox_path,
                    "logs": stage_logs,
                    "error_signature": "hot_reload_failed",
                    "changed_files": changed_files,
                    "migration": migration_result,
                }

        # 6) health check + rollback
        health_check_commands = payload.get("health_check_commands") or []
        if health_check_commands:
            ok, logs = self._run_stage("health_check", health_check_commands, self.repo_root, timeout_sec)
            stage_logs.extend(logs)
            if not ok:
                rollback_commands = payload.get("rollback_commands") or []
                rb_ok, rb_logs = self._run_stage("rollback", rollback_commands, self.repo_root, timeout_sec)
                stage_logs.extend(rb_logs)
                restored = self._restore_files(file_backups)
                stage_logs.append(
                    {
                        "stage": "rollback_files",
                        "command": "restore_files_after_health_failed",
                        "ok": True,
                        "exit_code": 0,
                        "stdout": f"restored={len(restored)}",
                        "stderr": "",
                        "elapsed_ms": 0,
                    }
                )
                migration_rb = self._rollback_db_migration(migration_result)
                stage_logs.append(
                    {
                        "stage": "rollback_migration",
                        "command": "down_sql",
                        "ok": bool(migration_rb.get("ok", False) or not migration_rb.get("attempted")),
                        "exit_code": 0 if (migration_rb.get("ok", False) or not migration_rb.get("attempted")) else 1,
                        "stdout": "migration rolled back" if migration_rb.get("ok") else "",
                        "stderr": migration_rb.get("error", ""),
                        "elapsed_ms": 0,
                    }
                )
                return {
                    "ok": False,
                    "task_status": "rolled_back" if rb_ok else "failed",
                    "sandbox_path": sandbox_path,
                    "logs": stage_logs,
                    "error_signature": "health_check_failed",
                    "changed_files": changed_files,
                    "migration": migration_result,
                }

        return {
            "ok": True,
            "task_status": "success",
            "sandbox_path": sandbox_path,
            "logs": stage_logs,
            "error_signature": None,
            "changed_files": changed_files,
            "migration": migration_result,
        }


def dump_logs_to_file(result: Dict[str, Any]) -> str:
    sandbox_path = result.get("sandbox_path") or _sandbox_root()
    path = os.path.join(sandbox_path, "run-log.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return path

