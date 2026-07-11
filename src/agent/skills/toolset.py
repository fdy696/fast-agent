"""Skill 工具集 — 将 SkillRegistry 暴露为 pydantic-ai Toolset."""
import re
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.messages import InstructionPart
from pydantic_ai.tools import RunContext


def _empty_validator():
    from pydantic import TypeAdapter
    return TypeAdapter(dict).validator


def _safe_tool_name(name: str) -> str:
    return re.sub(r'[^a-zA-Z0-9_]', '_', name)


def _make_loader(registry, name: str):
    async def load() -> str:
        try:
            body = registry.get_skill_prompt(name)
        except Exception:
            return f"无法加载能力: {name}"
        if not body:
            return f"未知能力: {name}"
        skill = registry.skills.get(name)
        if skill and skill.auto_load_references:
            refs = []
            for ref_name in skill.auto_load_references:
                try:
                    refs.append(registry.get_skill_reference(name, ref_name))
                except Exception:
                    pass
            if refs:
                body += "\n\n" + "\n\n".join(refs)
        return body
    return load


class SkillToolset(AbstractToolset):

    def __init__(self, registry):
        self.registry = registry
        self._loaders: dict = {}
        self._tool_to_skill: dict = {}  # tool_name → skill_name

    @property
    def id(self) -> str | None:
        return None

    async def get_instructions(self, ctx: RunContext[None]):
        meta = self.registry.build_skill_metadata()
        if not meta or not meta.strip():
            return []
        hint = (
            "\n\n当用户切换话题或当前技能不再适用时：\n"
            "- 不再引用之前技能的指令\n"
            "- 如需新技能，先调用 load_<name> 获取规则\n"
            "- 忘记旧技能的具体约束，只保留一般能力"
        )
        return [InstructionPart(content=meta + hint)]

    async def get_tools(self, ctx: RunContext[None]):
        tools = {}
        for name in self.registry.skills:
            tool_name = f"load_{_safe_tool_name(name)}"
            self._loaders[name] = _make_loader(self.registry, name)
            self._tool_to_skill[tool_name] = name
            tools[tool_name] = ToolsetTool(
                toolset=self,
                tool_def=ToolDefinition(
                    name=tool_name,
                    description=f"加载 '{name}' 能力的完整指令",
                ),
                max_retries=1,
                args_validator=_empty_validator(),
            )
        return tools

    async def call_tool(self, name, tool_args, ctx, tool):
        return await self._loaders[self._tool_to_skill[name]]()
