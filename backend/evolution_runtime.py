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
        for src_file, rel_path in self._iter_allowed_sandbox_files(sandbox_path, allow_paths):
            changed_rel_path = self._sync_single_file(src_file, rel_path, backups)
            if changed_rel_path:
                changed_files.append(changed_rel_path)
        return changed_files, backups

    def _iter_allowed_sandbox_files(self, sandbox_path: str, allow_paths: List[str]):
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
                    yield src_file, rel_path

    def _sync_single_file(
        self,
        src_file: str,
        rel_path: str,
        backups: Dict[str, Dict[str, Any]],
    ) -> str:
        dst_file = os.path.join(self.repo_root, rel_path)
        os.makedirs(os.path.dirname(dst_file), exist_ok=True)
        existed = os.path.exists(dst_file)
        before = None
        if existed:
            with open(dst_file, "rb") as f:
                before = f.read()
        with open(src_file, "rb") as f:
            after = f.read()
        if existed and before == after:
            return ""
        backups[rel_path] = {"existed": existed, "content": before}
        with open(dst_file, "wb") as f:
            f.write(after)
        return rel_path.replace("\\", "/")

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

    def _append_system_stage_log(
        self,
        stage_logs: List[Dict[str, Any]],
        stage: str,
        command: str,
        ok: bool,
        stdout: str = "",
        stderr: str = "",
    ):
        stage_logs.append(
            {
                "stage": stage,
                "command": command,
                "ok": ok,
                "exit_code": 0 if ok else 1,
                "stdout": stdout,
                "stderr": stderr,
                "elapsed_ms": 0,
            }
        )

    def _build_result(
        self,
        ok: bool,
        task_status: str,
        sandbox_path: str,
        logs: List[Dict[str, Any]],
        error_signature: Any,
        changed_files: List[str],
        migration: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "ok": ok,
            "task_status": task_status,
            "sandbox_path": sandbox_path,
            "logs": logs,
            "error_signature": error_signature,
            "changed_files": changed_files,
            "migration": migration,
        }

    def _run_stage_or_fail(
        self,
        stage_name: str,
        commands: List[str],
        cwd: str,
        timeout_sec: int,
        sandbox_path: str,
        stage_logs: List[Dict[str, Any]],
        error_signature: str,
    ) -> Dict[str, Any]:
        ok, logs = self._run_stage(stage_name, commands, cwd, timeout_sec)
        stage_logs.extend(logs)
        if ok:
            return {}
        return self._build_result(
            ok=False,
            task_status="failed",
            sandbox_path=sandbox_path,
            logs=stage_logs,
            error_signature=error_signature,
            changed_files=[],
            migration={"enabled": False},
        )

    def _run_migration_or_fail(
        self,
        payload: Dict[str, Any],
        sandbox_path: str,
        stage_logs: List[Dict[str, Any]],
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        migration_result = self._apply_db_migration(payload)
        if migration_result.get("enabled"):
            self._append_system_stage_log(
                stage_logs,
                stage="db_migration",
                command=str(migration_result.get("name")),
                ok=bool(migration_result.get("ok", False)),
                stdout="migration applied" if migration_result.get("ok") else "",
                stderr=str(migration_result.get("error", "")),
            )
            if not migration_result.get("ok"):
                return migration_result, self._build_result(
                    ok=False,
                    task_status="failed",
                    sandbox_path=sandbox_path,
                    logs=stage_logs,
                    error_signature="db_migration_failed",
                    changed_files=[],
                    migration=migration_result,
                )
        return migration_result, {}

    def _run_hot_reload_or_rollback(
        self,
        payload: Dict[str, Any],
        timeout_sec: int,
        stage_logs: List[Dict[str, Any]],
        sandbox_path: str,
        file_backups: Dict[str, Dict[str, Any]],
        migration_result: Dict[str, Any],
        changed_files: List[str],
    ) -> Dict[str, Any]:
        hot_reload_commands = payload.get("hot_reload_commands") or []
        if not hot_reload_commands:
            return {}
        ok, logs = self._run_stage("hot_reload", hot_reload_commands, self.repo_root, timeout_sec)
        stage_logs.extend(logs)
        if ok:
            return {}
        return self._rollback_after_failure(
            rollback_reason="restore_files_after_hot_reload_failed",
            error_signature="hot_reload_failed",
            final_task_status="rolled_back",
            sandbox_path=sandbox_path,
            stage_logs=stage_logs,
            file_backups=file_backups,
            migration_result=migration_result,
            changed_files=changed_files,
        )

    def _run_health_check_or_rollback(
        self,
        payload: Dict[str, Any],
        timeout_sec: int,
        stage_logs: List[Dict[str, Any]],
        sandbox_path: str,
        file_backups: Dict[str, Dict[str, Any]],
        migration_result: Dict[str, Any],
        changed_files: List[str],
    ) -> Dict[str, Any]:
        health_check_commands = payload.get("health_check_commands") or []
        if not health_check_commands:
            return {}
        ok, logs = self._run_stage("health_check", health_check_commands, self.repo_root, timeout_sec)
        stage_logs.extend(logs)
        if ok:
            return {}
        rollback_commands = payload.get("rollback_commands") or []
        rb_ok, rb_logs = self._run_stage("rollback", rollback_commands, self.repo_root, timeout_sec)
        stage_logs.extend(rb_logs)
        return self._rollback_after_failure(
            rollback_reason="restore_files_after_health_failed",
            error_signature="health_check_failed",
            final_task_status="rolled_back" if rb_ok else "failed",
            sandbox_path=sandbox_path,
            stage_logs=stage_logs,
            file_backups=file_backups,
            migration_result=migration_result,
            changed_files=changed_files,
        )

    def _rollback_after_failure(
        self,
        rollback_reason: str,
        error_signature: str,
        final_task_status: str,
        sandbox_path: str,
        stage_logs: List[Dict[str, Any]],
        file_backups: Dict[str, Dict[str, Any]],
        migration_result: Dict[str, Any],
        changed_files: List[str],
    ) -> Dict[str, Any]:
        restored = self._restore_files(file_backups)
        self._append_system_stage_log(
            stage_logs,
            stage="rollback_files",
            command=rollback_reason,
            ok=True,
            stdout=f"restored={len(restored)}",
        )
        migration_rb = self._rollback_db_migration(migration_result)
        migration_ok = bool(migration_rb.get("ok", False) or not migration_rb.get("attempted"))
        self._append_system_stage_log(
            stage_logs,
            stage="rollback_migration",
            command="down_sql",
            ok=migration_ok,
            stdout="migration rolled back" if migration_rb.get("ok") else "",
            stderr=str(migration_rb.get("error", "")),
        )
        return self._build_result(
            ok=False,
            task_status=final_task_status,
            sandbox_path=sandbox_path,
            logs=stage_logs,
            error_signature=error_signature,
            changed_files=changed_files,
            migration=migration_result,
        )

    def execute_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        payload = task.get("payload") or {}
        timeout_sec = int(payload.get("timeout_sec", 300))
        sandbox_path = self.create_sandbox(task["id"])
        stage_logs: List[Dict[str, Any]] = []
        changed_files: List[str] = []
        file_backups: Dict[str, Dict[str, Any]] = {}

        commands = payload.get("commands") or []
        early_result = self._run_stage_or_fail(
            "commands",
            commands,
            sandbox_path,
            timeout_sec,
            sandbox_path,
            stage_logs,
            "commands_failed",
        )
        if early_result:
            return early_result

        verify_commands = payload.get("verify_commands") or []
        early_result = self._run_stage_or_fail(
            "verify",
            verify_commands,
            sandbox_path,
            timeout_sec,
            sandbox_path,
            stage_logs,
            "verify_failed",
        )
        if early_result:
            return early_result

        migration_result, early_result = self._run_migration_or_fail(payload, sandbox_path, stage_logs)
        if early_result:
            return early_result

        # 注意：allow_write_paths 显式传 [] 时表示“禁止回写”
        allow_paths = payload.get("allow_write_paths")
        if allow_paths is None:
            allow_paths = ["frontend", "backend", "plugins", "docs"]
        changed_files, file_backups = self._copy_back_changes(sandbox_path=sandbox_path, allow_paths=allow_paths)
        self._append_system_stage_log(
            stage_logs,
            stage="sync_to_repo",
            command="sandbox->repo",
            ok=True,
            stdout=f"changed_files={len(changed_files)}",
        )

        early_result = self._run_hot_reload_or_rollback(
            payload=payload,
            timeout_sec=timeout_sec,
            stage_logs=stage_logs,
            sandbox_path=sandbox_path,
            file_backups=file_backups,
            migration_result=migration_result,
            changed_files=changed_files,
        )
        if early_result:
            return early_result

        early_result = self._run_health_check_or_rollback(
            payload=payload,
            timeout_sec=timeout_sec,
            stage_logs=stage_logs,
            sandbox_path=sandbox_path,
            file_backups=file_backups,
            migration_result=migration_result,
            changed_files=changed_files,
        )
        if early_result:
            return early_result

        return self._build_result(
            ok=True,
            task_status="success",
            sandbox_path=sandbox_path,
            logs=stage_logs,
            error_signature=None,
            changed_files=changed_files,
            migration=migration_result,
        )


def dump_logs_to_file(result: Dict[str, Any]) -> str:
    sandbox_path = result.get("sandbox_path") or _sandbox_root()
    path = os.path.join(sandbox_path, "run-log.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return path

