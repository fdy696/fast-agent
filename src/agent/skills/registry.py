"""
Skill 注册表 —— 基于 SkillMeta 字典。
"""

from __future__ import annotations

from .loader import SkillMeta, scan_skill_dirs


class SkillRegistry:
    def __init__(self) -> None:
        self.skills: dict[str, SkillMeta] = {}

    def scan_skills(self) -> None:
        self.skills = scan_skill_dirs()

    # ---------- L1 ----------

    def build_skill_metadata(self) -> str:
        """L1：生成所有 Skill 的 name + description 列表"""
        lines = ["## 可用能力"]
        for skill in self.skills.values():
            lines.append(f"- **{skill.name}**: {skill.description}")
        return "\n".join(lines)

    # ---------- L2 ----------

    def get_skill_prompt(self, name: str) -> str:
        """L2：返回缓存的 SKILL.md 正文"""
        if name not in self.skills:
            return ""
        return self.skills[name].body

    # ---------- L3 ----------

    def get_skill_reference(self, name: str, ref: str) -> str:
        """L3：读取 references/ 下的文件，带路径穿越防护"""
        skill = self.skills[name]
        ref_path = (skill.path / ref).resolve()
        skill_root = skill.path.resolve()

        if not str(ref_path).startswith(str(skill_root)):
            raise ValueError(f"拒绝访问 Skill 目录外的路径: {ref}")

        return ref_path.read_text(encoding="utf-8")

    # ---------- PydanticAI Tool 转换 ----------

    def as_pydantic_ai_tools(self) -> list:
        """将每个 skill 作为一个 function tool 注入 agent。"""
        tools: list = []
        for skill in self.skills.values():
            def _make_skill_fn(name=skill.name, body=skill.body):
                def skill_fn():
                    return body
                return skill_fn

            tools.append((_make_skill_fn(), skill.name, skill.description))
        return tools
