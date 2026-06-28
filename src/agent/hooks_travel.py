"""travel-planner 专用 hook 回调 — 不污染 agent_loop"""

import json

_skip_counter: int = 0
_SKIP_ALERT: int = 10


def _check_skill_gap() -> None:
    """agent_loop 入口调用：连续 N 次请求未触发 load_skill 时告警。"""
    global _skip_counter
    _skip_counter += 1
    if _skip_counter >= _SKIP_ALERT:
        from log import logger
        logger.warning(f"连续 {_skip_counter} 次请求未触发 load_skill，L1 引导可能失效")


def activate_skill(tc: dict, active_skills: set, active_refs: set, registry) -> None:
    """PostToolUse：load_skill 调用后激活对应 skill 的 L2/L3。"""
    from log import logger  # 懒加载避免循环导入

    global _skip_counter
    tool_name = tc["function"]["name"]

    if tool_name != "load_skill":
        return

    _skip_counter = 0  # 成功触发即重置

    raw_args = tc["function"].get("arguments", "{}")
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    except (json.JSONDecodeError, TypeError):
        return
    skill_name = args.get("name", "")
    logger.info(f"skill activated via load_skill: {skill_name}")

    if not skill_name or skill_name not in registry.skills:
        return

    active_skills.add(skill_name)
    for ref in registry.skills[skill_name].auto_load_references:
        active_refs.add((skill_name, ref))
