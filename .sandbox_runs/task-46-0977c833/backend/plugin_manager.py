import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


class PluginManager:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root).resolve()
        self.plugins_dir = self.project_root / "plugins"
        self.trash_dir = self.plugins_dir / ".trash"
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.trash_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _validate_plugin_id(plugin_id: str):
        if not plugin_id or not re.fullmatch(r"[a-zA-Z0-9._-]+", plugin_id):
            raise ValueError("plugin_id 非法，只允许字母、数字、.、_、-")

    @staticmethod
    def _safe_relative_path(raw_path: str) -> Path:
        candidate = Path(raw_path.replace("\\", "/")).as_posix().strip("/")
        path = Path(candidate)
        if not candidate or ".." in path.parts:
            raise ValueError(f"文件路径非法: {raw_path}")
        return path

    def install_plugin(self, manifest: Dict[str, Any], files: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        plugin_id = manifest.get("plugin_id")
        self._validate_plugin_id(plugin_id)
        plugin_dir = self.plugins_dir / plugin_id
        plugin_dir.mkdir(parents=True, exist_ok=True)

        normalized_manifest = {
            "plugin_id": plugin_id,
            "name": manifest.get("name") or plugin_id,
            "version": manifest.get("version") or "0.1.0",
            "runtime": manifest.get("runtime") or "python",
            "entrypoint": manifest.get("entrypoint") or "main.py",
            "enabled": bool(manifest.get("enabled", False)),
            "capabilities": manifest.get("capabilities") or [],
            "permissions": manifest.get("permissions") or {},
            "schedules": manifest.get("schedules") or [],
            "task_templates": manifest.get("task_templates") or [],
        }

        (plugin_dir / "manifest.json").write_text(
            json.dumps(normalized_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        for relative, content in (files or {}).items():
            rel_path = self._safe_relative_path(relative)
            file_path = plugin_dir / rel_path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")

        return {
            "plugin_id": normalized_manifest["plugin_id"],
            "name": normalized_manifest["name"],
            "version": normalized_manifest["version"],
            "runtime": normalized_manifest["runtime"],
            "manifest": normalized_manifest,
            "install_path": str(plugin_dir),
        }

    def set_enabled(self, plugin_id: str, enabled: bool) -> Dict[str, Any]:
        self._validate_plugin_id(plugin_id)
        manifest_path = self.plugins_dir / plugin_id / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"插件不存在: {plugin_id}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["enabled"] = bool(enabled)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return manifest

    def uninstall_plugin(self, plugin_id: str) -> str:
        self._validate_plugin_id(plugin_id)
        plugin_dir = self.plugins_dir / plugin_id
        if not plugin_dir.exists():
            return ""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archived = self.trash_dir / f"{plugin_id}-{timestamp}"
        if archived.exists():
            shutil.rmtree(archived)
        shutil.move(str(plugin_dir), str(archived))
        return str(archived)
