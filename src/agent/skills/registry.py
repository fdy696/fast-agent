from dataclasses import dataclass, field
from pathlib import Path
import yaml

from log import logger

SKILLS_DIR = Path(__file__).parent


@dataclass
class SkillMeta:
    name: str
    path: Path
    description: str
    tools: list[str] = field(default_factory=list)
    auto_load_references: list[str] = field(default_factory=list)
    body: str = ""  # 缓存 SKILL.md 正文，避免重复读取


class SkillRegistry:
    def __init__(self):
        self.skills: dict[str, SkillMeta] = {}

    def scan_skills(self):
        """扫描所有子目录的 SKILL.md，建立 skills 注册表"""
        for skill_dir in sorted(SKILLS_DIR.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue

            raw = skill_file.read_text(encoding="utf-8")
            meta, body = self._parse_frontmatter(raw)

            self.skills[meta["name"]] = SkillMeta(
                name=meta["name"],
                path=skill_dir,
                description=meta.get("description", ""),
                tools=meta.get("tools", []),
                auto_load_references=meta.get("auto_load_references", []),
                body=body.strip(),
            )

        logger.info(f"loaded {len(self.skills)} skills")

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

    # ---------- 内部 ----------

    def _parse_frontmatter(self, raw: str) -> tuple[dict, str]:
        if not raw.startswith("---"):
            return {}, raw

        parts = raw.split("---", 2)
        if len(parts) < 3:
            return {}, raw

        _, fm, body = parts
        return yaml.safe_load(fm) or {}, body.strip()


# 全局单例
registry = SkillRegistry()
