"""
技能商店：从 GitHub 拉取 SKILL.md 并安装
"""
import os
import re
import json
from pathlib import Path
from typing import Optional, List, Dict, Any
import httpx

# 技能目录的常见路径（按优先级）
SKILL_FOLDER_NAMES = [".agent-skills", "skills", ".agents/skills", ".claude/skills"]
GITHUB_RAW_BASE = "https://raw.githubusercontent.com"
GITHUB_API_BASE = "https://api.github.com"
REQUEST_TIMEOUT = 15.0


def _proxy_url(proxy: Optional[Dict]) -> Optional[str]:
    """从代理配置构建 httpx 可用的 proxy URL"""
    if not proxy:
        return None
    from proxy_utils import build_proxy_url, should_skip_proxy
    if should_skip_proxy(proxy, GITHUB_API_BASE):
        return None
    return build_proxy_url(proxy) or None


def _load_recommended_skills() -> List[Dict]:
    """加载内置推荐技能列表"""
    data_path = Path(__file__).parent / "data" / "recommended_skills.json"
    if not data_path.exists():
        return []
    try:
        with open(data_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def get_recommended_skills(query: str = "") -> List[Dict]:
    """获取推荐技能，支持关键词过滤"""
    skills = _load_recommended_skills()
    if query:
        q = query.lower()
        skills = [
            s for s in skills
            if q in (s.get("name") or "").lower()
            or q in (s.get("description") or "").lower()
            or q in (s.get("description_zh") or "").lower()
        ]
    return skills


def _parse_repo(repo_input: str) -> Optional[tuple]:
    """解析 repo 输入为 (owner, repo, branch)"""
    repo_input = repo_input.strip().rstrip("/")
    # 移除 URL 前缀
    for prefix in ["https://github.com/", "http://github.com/", "github.com/"]:
        if repo_input.lower().startswith(prefix):
            repo_input = repo_input[len(prefix):]
    parts = repo_input.split("/")
    if len(parts) >= 2:
        owner, repo = parts[0], parts[1]
        if repo.endswith(".git"):
            repo = repo[:-4]
        branch = parts[2] if len(parts) > 2 else "main"
        return (owner, repo, branch)
    if len(parts) == 1 and "/" in repo_input:
        owner, repo = repo_input.split("/", 1)
        return (owner, repo, "main")
    return None


def _fetch_github_json(url: str, token: Optional[str] = None, proxy: Optional[Dict] = None) -> Optional[Any]:
    """请求 GitHub API 返回 JSON"""
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    proxy_url = _proxy_url(proxy)
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=True, proxy=proxy_url) as client:
            r = client.get(url, headers=headers)
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    return None


def _fetch_raw(url: str, proxy: Optional[Dict] = None) -> Optional[str]:
    """拉取 raw 文件内容"""
    proxy_url = _proxy_url(proxy)
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=True, proxy=proxy_url) as client:
            r = client.get(url)
            if r.status_code == 200:
                return r.text
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"[skill_store] _fetch_raw failed: url={url!r} proxy={bool(proxy_url)} err={e!r}")
    return None


def list_skills_from_github(repo: str, token: Optional[str] = None, proxy: Optional[Dict] = None) -> List[Dict]:
    """
    从 GitHub 仓库列出所有技能。
    尝试 .agent-skills、skills、.agents/skills 等目录。
    """
    parsed = _parse_repo(repo)
    if not parsed:
        return []
    owner, repo_name, branch = parsed
    base_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo_name}/contents"
    skills_found = []

    for folder in SKILL_FOLDER_NAMES:
        parts = folder.split("/")
        api_path = "/".join(parts)
        url = f"{base_url}/{api_path}?ref={branch}"
        data = _fetch_github_json(url, token, proxy)
        if not data:
            continue
        items = data if isinstance(data, list) else []
        for item in items:
            if item.get("type") == "dir" and item["name"] not in (".", "..", "README.md"):
                skill_name = item["name"]
                skills_found.append({
                    "name": skill_name,
                    "source": f"{owner}/{repo_name}",
                    "skill_path": folder,
                    "description": "",
                    "description_zh": ""
                })
        if skills_found:
            break

    return skills_found


def _parse_skill_md(content: str) -> tuple:
    """解析 SKILL.md：提取 frontmatter 的 name、description 和正文 content"""
    name, description = "", ""
    body = content
    if content.strip().startswith("---"):
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
        if match:
            fm_str, body = match.group(1), match.group(2)
            for line in fm_str.split("\n"):
                if line.startswith("name:"):
                    name = line[4:].strip().strip('"\'')
                elif line.startswith("description:"):
                    description = line[11:].strip().strip('"\'')
    return (name, description, body.strip())


def fetch_skill_content(source: str, skill_name: str, skill_path: str = ".agent-skills", proxy: Optional[Dict] = None) -> Optional[Dict]:
    """
    从 GitHub 拉取技能完整内容。
    source: owner/repo
    skill_path: 技能目录如 .agent-skills 或 skills
    返回 {name, description, content} 或 None
    """
    parsed = _parse_repo(source)
    if not parsed:
        return None
    owner, repo_name, branch = parsed
    path_part = f"{skill_path}/{skill_name}" if skill_path else skill_name
    raw_url = f"{GITHUB_RAW_BASE}/{owner}/{repo_name}/{branch}/{path_part}/SKILL.md"
    text = _fetch_raw(raw_url, proxy)
    if not text:
        return None
    name, description, content = _parse_skill_md(text)
    return {
        "name": name or skill_name,
        "description": description,
        "content": content or text,
    }


def install_skill(
    source: str,
    skill_name: str,
    skill_path: str,
    description_zh: Optional[str],
    bound_device_type_ids: List[int],
    db,
    proxy: Optional[Dict] = None,
) -> Optional[int]:
    """
    安装技能：拉取 content，写入数据库，绑定设备类型。
    返回 skill_id 或 None
    """
    data = fetch_skill_content(source, skill_name, skill_path, proxy)
    if not data:
        return None
    existing = db.get_skill_by_name(skill_name)
    if existing:
        merged = {
            **existing,
            "display_name": data["name"],
            "description": data["description"],
            "description_zh": description_zh,
            "content": data["content"],
            "source": "github",
            "source_url": source,
            "skill_path": skill_path,
            "is_enabled": 1,
        }
        db.update_skill(existing["id"], merged)
        db.update_skill_device_type_bindings(existing["id"], bound_device_type_ids)
        return existing["id"]
    skill_data = {
        "name": skill_name,
        "display_name": data["name"],
        "description": data["description"],
        "description_zh": description_zh,
        "content": data["content"],
        "source": "github",
        "source_url": source,
        "skill_path": skill_path,
        "is_enabled": 1,
    }
    skill_id = db.add_skill(skill_data)
    if bound_device_type_ids:
        db.update_skill_device_type_bindings(skill_id, bound_device_type_ids)
    return skill_id
