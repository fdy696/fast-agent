"""load_skill — 模型主动加载 skill 工作流的本地工具"""

from agent.skills.registry import registry
from log import logger


async def load_skill(name: str) -> dict:
    """返回指定 skill 的完整 SKILL.md 正文（含 L2 + 内联的 L3 references）。"""
    
    logger.info(f"llm load skill {name}")
    
    skill = registry.skills.get(name)
    if not skill:
        return {"error": f"未知能力: {name}"}

    body = registry.get_skill_prompt(name)

    # 内联 auto_load_references
    for ref in skill.auto_load_references:
        try:
            body += f"\n\n---\n# 参考: {ref}\n" + registry.get_skill_reference(name, ref)
        except (ValueError, OSError):
            pass

    return {"skill": name, "content": body}
