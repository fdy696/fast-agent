"""
Skill 加载器 —— 从 SKILL.md 文件加载 skill 元数据和正文。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from log import logger

SKILLS_DIR = Path(__file__).resolve().parent


@dataclass
class SkillMeta:
    name: str
    path: Path
    description: str
    tools: list[str] = field(default_factory=list)
    auto_load_references: list[str] = field(default_factory=list)
    body: str = ""


def scan_skill_dirs(base_dir: Path | None = None) -> dict[str, SkillMeta]:
    """扫描 base_dir 下所有子目录的 SKILL.md。"""
    if base_dir is None:
        base_dir = SKILLS_DIR

    skills: dict[str, SkillMeta] = {}
    if not base_dir.is_dir():
        return skills

    for skill_dir in sorted(base_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            continue

        raw = skill_file.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(raw)
        name = meta.get("name", skill_dir.name)

        skills[name] = SkillMeta(
            name=name,
            path=skill_dir,
            description=meta.get("description", ""),
            tools=meta.get("tools", []),
            auto_load_references=meta.get("auto_load_references", []),
            body=body.strip(),
        )

    logger.info(f"loaded {len(skills)} skills")
    return skills


def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    if not raw.startswith("---"):
        return {}, raw
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw
    _, fm, body = parts
    return yaml.safe_load(fm) or {}, body.strip()
