"""
全局 Skill 注册表单例。
"""

from .loader import SkillMeta, scan_skill_dirs
from .registry import SkillRegistry

# ---------- 全局单例 ----------

registry = SkillRegistry()
