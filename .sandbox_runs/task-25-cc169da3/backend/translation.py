"""
技能描述翻译：预置词汇 + 可选 AI 翻译
支持离线和内网环境降级。
"""
import json
from pathlib import Path
from typing import Optional, Tuple

# 预置英→中翻译（来自 recommended_skills + 常见运维术语）
PRESET_TRANSLATIONS: dict[str, str] = {
    "Analyze application logs to identify errors, performance issues, and security anomalies.": "分析应用日志，定位错误、性能问题与安全异常",
    "Systematically debug code issues using proven methodologies.": "系统化调试代码问题，采用成熟方法论",
    "Set up monitoring, logging, and observability for applications.": "搭建监控、日志与可观测性体系",
    "Automate application deployment to cloud and servers.": "自动化应用部署至云端与服务器",
    "Implement security best practices for web applications.": "实施 Web 应用安全最佳实践",
    "Automate repetitive development tasks and workflows.": "脚本、Makefile、任务编排",
    "Network architecture and engineering for switches, routers, firewalls.": "网络架构与工程，含交换机、路由器、防火墙",
    "Configure development and production environments for consistent and reproducible setups.": "环境搭建、Docker、开发/生产环境配置",
    "When user asks about logs": "当用户询问日志相关问题时",
    "When debugging": "当需要调试时",
    "Use when": "当需要",
    "Analyze logs": "分析日志",
    "Debug code": "调试代码",
    "Monitor applications": "监控应用",
    "Deploy applications": "部署应用",
    "Security practices": "安全最佳实践",
    "Automate workflows": "自动化工作流",
    "Network engineering": "网络工程",
}


def _load_preset_from_json() -> None:
    """从 recommended_skills.json 补充预置翻译"""
    data_path = Path(__file__).parent / "data" / "recommended_skills.json"
    if not data_path.exists():
        return
    try:
        with open(data_path, "r", encoding="utf-8") as f:
            for item in json.load(f):
                desc = item.get("description")
                desc_zh = item.get("description_zh")
                if desc and desc_zh:
                    PRESET_TRANSLATIONS[desc.strip()] = desc_zh.strip()
    except Exception:
        pass


_load_preset_from_json()


def translate_preset(text: str) -> Optional[str]:
    """预置翻译：精确匹配或去除首尾空白后匹配"""
    if not text or not text.strip():
        return None
    t = text.strip()
    if t in PRESET_TRANSLATIONS:
        return PRESET_TRANSLATIONS[t]
    # 允许首尾空白
    for k, v in PRESET_TRANSLATIONS.items():
        if k.strip() == t:
            return v
    return None


async def translate_with_ai(text: str, ai_handler) -> Optional[str]:
    """使用 AI 端点翻译（需传入已配置的 AIHandler 实例）"""
    if not text or not text.strip():
        return None
    prompt = f'''Translate the following English text to Simplified Chinese. Return ONLY the Chinese translation, no explanation, no quotes.

Text:
{text.strip()}

Chinese:'''
    try:
        result = []
        async for chunk in ai_handler.get_response_stream([{"role": "user", "content": prompt}]):
            result.append(chunk)
        translation = "".join(result).strip()
        if translation and len(translation) > 0:
            return translation
    except Exception:
        pass
    return None


async def translate_to_chinese(text: str, ai_handler=None) -> Tuple[Optional[str], str]:
    """
    翻译英文为中文。
    优先预置，其次 AI（若配置），失败时返回 (None, 提示信息)。
    """
    if not text or not text.strip():
        return None, "请输入要翻译的文本"

    # 1. 预置
    preset = translate_preset(text)
    if preset:
        return preset, ""

    # 2. AI
    if ai_handler:
        ai_result = await translate_with_ai(text, ai_handler)
        if ai_result:
            return ai_result, ""

    return None, "翻译服务暂不可用（离线/内网或未配置 AI），请手动填写中文描述"
