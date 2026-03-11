from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
import json
from pydantic import BaseModel

from ai_handler import AIHandler
from core import (
    AIModel,
    AITestModel,
    RoleModel,
    SKILL_NOT_FOUND_DETAIL,
    SkillInstallModel,
    SkillModel,
    TranslateModel,
    _get_skills_proxy,
    db,
    verify_token,
)
from logger import app_logger
from skill_store import (
    fetch_skill_content,
    get_recommended_skills,
    install_skill as do_install_skill,
    list_skills_from_github,
)
from translation import translate_to_chinese as do_translate


router = APIRouter()
ROLE_SCOPE_OPS = "ops"
ROLE_SCOPE_EVOLUTION = "evolution_dialog"
ROLE_NOT_FOUND = "Role not found"


class RoleImportModel(BaseModel):
    roles: list[RoleModel]


def _normalize_role_scope(scope: Optional[str]) -> str:
    val = (scope or ROLE_SCOPE_OPS).strip()
    if val not in (ROLE_SCOPE_OPS, ROLE_SCOPE_EVOLUTION):
        return ROLE_SCOPE_OPS
    return val


def _normalize_scope_tags(raw) -> list[str]:
    if raw is None:
        return ["ops"]
    if isinstance(raw, str):
        items = [x.strip().lower() for x in raw.split(",") if x.strip()]
    elif isinstance(raw, list):
        items = [str(x).strip().lower() for x in raw if str(x).strip()]
    else:
        items = []
    allowed = {"ops", "task", "plugin", "evolution"}
    cleaned = []
    for x in items:
        if x in allowed and x not in cleaned:
            cleaned.append(x)
    return cleaned or ["ops"]


@router.get("/api/ai_endpoints", dependencies=[Depends(verify_token)])
async def list_ai():
    endpoints = db.get_all_ai_endpoints()
    for ep in endpoints:
        if ep["capabilities"]:
            try:
                ep["capabilities"] = json.loads(ep["capabilities"])
            except json.JSONDecodeError:
                ep["capabilities"] = ["text"]
    return endpoints


@router.get(
    "/api/ai_endpoints/{ai_id}",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": "AI Endpoint not found"}},
)
async def get_ai_endpoint(ai_id: int):
    ai = db.get_ai_endpoint_by_id(ai_id)
    if not ai:
        raise HTTPException(status_code=404, detail="AI Endpoint not found")
    if ai["capabilities"]:
        ai["capabilities"] = json.loads(ai["capabilities"])
    return ai


@router.post("/api/ai_endpoints", dependencies=[Depends(verify_token)])
async def add_ai(ai: AIModel):
    ai_id = db.add_ai_endpoint(ai.model_dump())
    return {"id": ai_id, "status": "success"}


@router.put("/api/ai_endpoints/{ai_id}", dependencies=[Depends(verify_token)])
async def update_ai(ai_id: int, ai: AIModel):
    db.update_ai_endpoint(ai_id, ai.model_dump())
    return {"status": "success"}


@router.delete("/api/ai_endpoints/{ai_id}", dependencies=[Depends(verify_token)])
async def delete_ai(ai_id: int):
    db.delete_ai_endpoint(ai_id)
    return {"status": "success"}


@router.post("/api/ai_endpoints/{ai_id}/activate", dependencies=[Depends(verify_token)])
async def activate_ai(ai_id: int):
    db.set_active_ai(ai_id)
    return {"status": "success"}


@router.post("/api/ai_endpoints/test", dependencies=[Depends(verify_token)])
async def test_ai(ai: AITestModel):
    proxy = None
    bindings = db.get_proxy_bindings()
    if bindings.get("ai"):
        proxy = db.get_proxy_by_id(bindings["ai"])
    handler = AIHandler(ai.api_key, ai.base_url, ai.model, "", proxy=proxy)
    success, message = await handler.test_connection()
    return {"success": success, "message": message}


@router.get("/api/roles", dependencies=[Depends(verify_token)])
async def list_roles(scope: Optional[str] = ROLE_SCOPE_OPS):
    role_scope = _normalize_role_scope(scope)
    return db.get_all_roles(scope=role_scope)


@router.get(
    "/api/roles/{role_id}",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": ROLE_NOT_FOUND}},
)
async def get_role(role_id: int):
    role = db.get_role_by_id(role_id)
    if not role:
        raise HTTPException(status_code=404, detail=ROLE_NOT_FOUND)
    return role


@router.post("/api/roles", dependencies=[Depends(verify_token)])
async def add_role(role: RoleModel, scope: Optional[str] = ROLE_SCOPE_OPS):
    role_scope = _normalize_role_scope(scope)
    data = role.model_dump()
    data["role_scope"] = role_scope
    bound_types = data.pop("bound_device_types", [])
    role_id = db.add_role(data)
    if bound_types and role_scope == ROLE_SCOPE_OPS:
        db.update_device_type_role_bindings(role_id, bound_types)
    return {"id": role_id, "status": "success"}


@router.put(
    "/api/roles/{role_id}",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": ROLE_NOT_FOUND}},
)
async def update_role(role_id: int, role: RoleModel, scope: Optional[str] = ROLE_SCOPE_OPS):
    role_scope = _normalize_role_scope(scope)
    if not db.get_role_by_id(role_id, scope=role_scope):
        raise HTTPException(status_code=404, detail=ROLE_NOT_FOUND)
    data = role.model_dump()
    data["role_scope"] = role_scope
    bound_types = data.pop("bound_device_types", [])
    db.update_role(role_id, data)
    if role_scope == ROLE_SCOPE_OPS:
        db.update_device_type_role_bindings(role_id, bound_types)
    return {"status": "success"}


@router.delete(
    "/api/roles/{role_id}",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": ROLE_NOT_FOUND}},
)
async def delete_role(role_id: int, scope: Optional[str] = ROLE_SCOPE_OPS):
    role_scope = _normalize_role_scope(scope)
    if not db.get_role_by_id(role_id, scope=role_scope):
        raise HTTPException(status_code=404, detail=ROLE_NOT_FOUND)
    db.delete_role(role_id)
    return {"status": "success"}


@router.post(
    "/api/roles/{role_id}/activate",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": ROLE_NOT_FOUND}},
)
async def activate_role(role_id: int, scope: Optional[str] = ROLE_SCOPE_OPS):
    role_scope = _normalize_role_scope(scope)
    if not db.get_role_by_id(role_id, scope=role_scope):
        raise HTTPException(status_code=404, detail=ROLE_NOT_FOUND)
    db.set_active_role(role_id, scope=role_scope)
    app_logger.info("角色管理", f"激活角色 ID: {role_id}, scope={role_scope}")
    return {"status": "success"}


@router.get("/api/roles/export", dependencies=[Depends(verify_token)])
async def export_roles(scope: Optional[str] = ROLE_SCOPE_EVOLUTION):
    role_scope = _normalize_role_scope(scope)
    roles = db.get_all_roles(scope=role_scope)
    for role in roles:
        role.pop("id", None)
        role.pop("created_at", None)
        role.pop("ai_name", None)
    return {"scope": role_scope, "roles": roles}


@router.post("/api/roles/import", dependencies=[Depends(verify_token)])
async def import_roles(
    body: RoleImportModel,
    scope: Optional[str] = ROLE_SCOPE_EVOLUTION,
    replace: bool = False,
):
    role_scope = _normalize_role_scope(scope)
    if replace:
        old_roles = db.get_all_roles(scope=role_scope)
        for role in old_roles:
            db.delete_role(role["id"])
    imported = 0
    for role in body.roles:
        data = role.model_dump()
        data["role_scope"] = role_scope
        data["is_active"] = int(data.get("is_active", 0))
        data.pop("bound_device_types", None)
        db.add_role(data)
        imported += 1
    return {"status": "success", "scope": role_scope, "imported": imported}


@router.get("/api/skills", dependencies=[Depends(verify_token)])
async def list_skills(enabled: Optional[int] = None, device_type_id: Optional[int] = None):
    """获取技能列表，支持 ?enabled=1 和 ?device_type_id=3 过滤"""
    enabled_only = enabled == 1 if enabled is not None else False
    return db.get_all_skills(enabled_only=enabled_only, device_type_id=device_type_id)


@router.get(
    "/api/skills/{skill_id}",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": SKILL_NOT_FOUND_DETAIL}},
)
async def get_skill(skill_id: int):
    skill = db.get_skill_by_id(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=SKILL_NOT_FOUND_DETAIL)
    return skill


@router.post(
    "/api/skills",
    dependencies=[Depends(verify_token)],
    responses={400: {"description": "技能名称已存在"}},
)
async def add_skill(skill: SkillModel):
    data = skill.model_dump()
    bound_types = data.pop("bound_device_types", [])
    data["scope_tags"] = _normalize_scope_tags(data.get("scope_tags"))
    if db.get_skill_by_name(data["name"]):
        raise HTTPException(status_code=400, detail=f"技能名称 '{data['name']}' 已存在")
    skill_id = db.add_skill(data)
    if bound_types:
        db.update_skill_device_type_bindings(skill_id, bound_types)
    app_logger.info("技能管理", f"创建技能: {data['name']}")
    return {"id": skill_id, "status": "success"}


@router.put(
    "/api/skills/{skill_id}",
    dependencies=[Depends(verify_token)],
    responses={
        400: {"description": "技能名称已存在"},
        404: {"description": SKILL_NOT_FOUND_DETAIL},
    },
)
async def update_skill(skill_id: int, skill: SkillModel):
    if not db.get_skill_by_id(skill_id):
        raise HTTPException(status_code=404, detail=SKILL_NOT_FOUND_DETAIL)
    data = skill.model_dump()
    bound_types = data.pop("bound_device_types", [])
    data["scope_tags"] = _normalize_scope_tags(data.get("scope_tags"))
    existing = db.get_skill_by_name(data["name"])
    if existing and existing["id"] != skill_id:
        raise HTTPException(status_code=400, detail=f"技能名称 '{data['name']}' 已存在")
    db.update_skill(skill_id, data)
    db.update_skill_device_type_bindings(skill_id, bound_types)
    app_logger.info("技能管理", f"更新技能 ID:{skill_id}")
    return {"status": "success"}


@router.delete(
    "/api/skills/{skill_id}",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": SKILL_NOT_FOUND_DETAIL}},
)
async def delete_skill(skill_id: int):
    if not db.get_skill_by_id(skill_id):
        raise HTTPException(status_code=404, detail=SKILL_NOT_FOUND_DETAIL)
    db.delete_skill(skill_id)
    app_logger.info("技能管理", f"删除技能 ID:{skill_id}")
    return {"status": "success"}


@router.post(
    "/api/skills/{skill_id}/toggle",
    dependencies=[Depends(verify_token)],
    responses={404: {"description": SKILL_NOT_FOUND_DETAIL}},
)
async def toggle_skill(skill_id: int):
    """切换技能启用/禁用状态"""
    if not db.get_skill_by_id(skill_id):
        raise HTTPException(status_code=404, detail=SKILL_NOT_FOUND_DETAIL)
    is_enabled = db.toggle_skill(skill_id)
    return {"is_enabled": is_enabled, "status": "success"}


@router.get("/api/skill_store/recommended", dependencies=[Depends(verify_token)])
async def skill_store_recommended(q: Optional[str] = ""):
    """获取推荐技能列表"""
    return get_recommended_skills(query=q or "")


@router.get("/api/skill_store/list", dependencies=[Depends(verify_token)])
async def skill_store_list(repo: str, token: Optional[str] = None):
    """从 GitHub 仓库列出技能"""
    proxy = _get_skills_proxy()
    skills = list_skills_from_github(repo, token=token, proxy=proxy)
    return skills


@router.post(
    "/api/skill_store/install",
    dependencies=[Depends(verify_token)],
    responses={502: {"description": "安装失败：无法拉取技能内容"}},
)
async def skill_store_install(install: SkillInstallModel):
    """安装技能（从 GitHub 拉取并写入数据库）"""
    proxy = _get_skills_proxy()
    scope_tags = _normalize_scope_tags(install.scope_tags)
    skill_id = do_install_skill(
        source=install.source,
        skill_name=install.skill_name,
        skill_path=install.skill_path,
        description_zh=install.description_zh,
        scope_tags=scope_tags,
        bound_device_type_ids=install.bound_device_type_ids,
        db=db,
        proxy=proxy,
    )
    if skill_id is None:
        raise HTTPException(
            status_code=502,
            detail="安装失败：无法拉取技能内容，请检查源地址或网络。若已配置技能代理，请确认代理能访问 raw.githubusercontent.com",
        )
    app_logger.info("技能管理", f"从商店安装技能: {install.skill_name}")
    return {"id": skill_id, "status": "success"}


@router.post("/api/translate", dependencies=[Depends(verify_token)])
async def translate_text(model: TranslateModel):
    """将英文技能描述翻译为中文。优先预置词汇，其次 AI（若已配置）。离线时返回提示。"""
    ai_handler = None
    ai = db.get_active_ai_endpoint()
    if ai:
        proxy = None
        bindings = db.get_proxy_bindings()
        if bindings.get("ai"):
            proxy = db.get_proxy_by_id(bindings["ai"])
        ai_handler = AIHandler(ai["api_key"], ai["base_url"], ai["model"], "", proxy=proxy)
    translation, message = await do_translate(model.text, ai_handler)
    if translation:
        return {"translation": translation}
    return {"translation": None, "message": message}


@router.post(
    "/api/skills/{skill_id}/refresh",
    dependencies=[Depends(verify_token)],
    responses={
        400: {"description": "技能源格式错误或不可刷新"},
        404: {"description": SKILL_NOT_FOUND_DETAIL},
        502: {"description": "无法从远程拉取技能内容"},
    },
)
async def refresh_skill(skill_id: int):
    """从 source_url 重新拉取 content（远程技能）"""
    skill = db.get_skill_by_id(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=SKILL_NOT_FOUND_DETAIL)
    source_url = skill.get("source_url")
    if not source_url:
        raise HTTPException(status_code=400, detail="该技能为本地创建，无远程源可刷新")
    parts = source_url.split("/")
    if len(parts) < 2:
        raise HTTPException(status_code=400, detail="无效的远程源格式")
    skill_name = skill.get("name") or ""
    skill_path = skill.get("skill_path") or ".agent-skills"
    proxy = _get_skills_proxy()
    data = fetch_skill_content(source_url, skill_name, skill_path, proxy)
    if not data:
        raise HTTPException(status_code=502, detail="无法从远程拉取技能内容，请检查网络或源地址")
    merged = {
        **skill,
        "display_name": data["name"],
        "description": data["description"],
        "content": data["content"],
    }
    db.update_skill(skill_id, merged)
    app_logger.info("技能管理", f"刷新技能 ID:{skill_id} 成功")
    return {"status": "success", "message": "已从远程更新技能内容"}
