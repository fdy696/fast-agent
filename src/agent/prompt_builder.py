"""动态 System Prompt 构建：BASE（含技能清单） + active L2/L3 指令"""


def build_runtime_prompt(
    base_prompt: str,
    active_skills: set[str],
    active_refs: set[tuple[str, str]],
    custom_prompt: str = "",         # ← 根据前端传入的商业化prompt特定增强
) -> str:
    from agent.skills.registry import registry

    parts = [base_prompt]

    if active_skills:
        parts.append("\n\n---\n## 当前激活的能力指令\n")
        for skill_name in sorted(active_skills):
            prompt = registry.get_skill_prompt(skill_name)
            if prompt:
                parts.append(f"\n### {skill_name}\n{prompt}")

    if active_refs:
        parts.append("\n\n---\n## 参考模板\n")
        for skill_name, ref in sorted(active_refs):
            content = registry.get_skill_reference(skill_name, ref)
            parts.append(f"\n### {skill_name} / {ref}\n{content}")

    return "\n".join(parts)
