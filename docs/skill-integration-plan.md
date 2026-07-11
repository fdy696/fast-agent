# Skill 系统集成 pydantic-ai — 实现计划

> 将现有 SkillRegistry 包装为 `AbstractToolset`，L1 指令 + L2/L3 工具动态加载

## 核心方案

`SkillToolset(AbstractToolset)` → Agent 通过 `toolsets` 参数注入。

- **L1**：`get_instructions()` 返回能力清单，pydantic-ai 注入 system prompt
- **L2/L3**：`get_tools()` 为每个 skill 注册 `load_<name>` 工具，LLM 按需调用

## 解决的问题

| # | 问题 | 修正 |
|---|------|------|
| 1 | `call_tool` 默认实现抛 `NotImplementedError`，不是通用实现 | **必须覆写** `call_tool`，正确调用 load 函数 |
| 2 | 空/空白 `meta` 注入无意义 `InstructionPart` | `meta.strip()` 检查后再返回 |
| 3 | `name.replace("-", "_")` 只处理连字符 | `re.sub(r'[^a-zA-Z0-9_]', '_', name)` 防御性清理 |
| 4 | `get_skill_prompt` 抛异常时 `if not body` 不生效 | `try-except` 兜底返回错误信息 |
| 5 | 旧模块级 `registry` 单例可能被其他模块引用 | 全局搜索清理，移除 `skills/__init__.py` 中的单例 |

## 改动文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/agent/skills/toolset.py` | **新建** | `SkillToolset` + `_make_loader` |
| `src/agent/skills/__init__.py` | 修改 | 移除模块级 `registry` 单例 |
| `src/agent/model_client.py` | 修改 | `init_agent(registry)` 签名 + toolsets 注入 |
| `src/core/init_app.py` | 修改 | 创建 registry 实例，传入 `init_agent` |

## 代码

### `src/agent/skills/__init__.py` — 移除单例

```python
"""Skill 加载与注册."""
from .loader import SkillMeta, scan_skill_dirs
from .registry import SkillRegistry
```

### `src/agent/skills/toolset.py`（新建）

```python
"""Skill 工具集 — 将 SkillRegistry 暴露为 pydantic-ai Toolset."""
import re
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.messages import InstructionPart
from pydantic_ai.tools import RunContext


def _empty_validator():
    from pydantic_core import SchemaValidator
    return SchemaValidator({"type": "object", "properties": {}})


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
                tool_def=ToolDefinition(name=tool_name,
                    description=f"加载 '{name}' 能力的完整指令"),
                max_retries=1,
                args_validator=_empty_validator(),
            )
        return tools

    async def call_tool(self, name, tool_args, ctx, tool):
        return await self._loaders[self._tool_to_skill[name]]()


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
```

关键变化：
- **必须覆写 `call_tool`** — 默认实现抛 `NotImplementedError`，`FunctionToolset.call_tool` 是私有实现无法复用
- **`_safe_tool_name`** — `re.sub` 清理所有非法字符
- **`try-except` 包裹 `get_skill_prompt`** — 防御性处理
- **`meta.strip()` 检查** — 空字符串不注入

### `src/agent/model_client.py` — 修改 `init_agent` 签名

```python
from agent.skills.toolset import SkillToolset


def init_agent(registry) -> Agent:
    global _agent
    if _agent is not None:
        return _agent
    ...
    toolsets = [*get_mcp_toolsets(), SkillToolset(registry)]
    agent = Agent(model, toolsets=toolsets)
    ...
```

### `src/core/init_app.py` — 创建 registry 并传入

```python
from agent.skills.registry import SkillRegistry
from agent.model_client import init_agent

registry = SkillRegistry()
registry.scan_skills()
init_agent(registry)
```

### 存量清理

全局搜索确认无残留引用：

```bash
grep -rn 'from agent.skills import registry' src/
# 预期：全部替换完毕，无输出
```

## 技能生命周期与遗忘机制

参考 Claude Code 的设计，技能需要在用完后"自觉遗忘"。

我们的实现用两层机制覆盖这一点：

| 机制 | L1 元指令 | L2/L3 上下文淘汰 |
|------|---------|-----------------|
| 何时生效 | 每次请求 | 对话进行中 |
| 原理 | 元指令告诉模型切换话题时忽略旧技能的 L2/L3 细节 | L2/L3 是工具调用结果，存在对话历史里，token 预算滚动后自然消失 |
| 影响 L1？ | **不影响**——L1 目录在 system prompt 里，每次请求重新注入，不参与上下文滚动 | L1 不在对话历史里 |
| 恢复 | L1 永远存在，模型随时知道有哪些技能 | 模型需要时重新调 `load_<name>` 拉回 L2/L3 |

关键区分：**元指令让模型忘记的是技能细节（L2 工作流步骤），不是技能清单（L1 目录）。** 模型永远知道自己能做什么，但不带着上次的详细步骤去处理新请求。

不需要显式的 delete/unload API——模型自带注意力机制，加上 L1 元指令引导，切换话题后旧技能细节就被忽略。需要时再加载，零成本恢复。

## 启动顺序

```
init_app.py:
  1. registry = SkillRegistry()
  2. registry.scan_skills()         ← 加载 SKILL.md 到内存
  3. init_agent(registry)           ← Agent 创建时注入 SkillToolset

首次请求:
  pydantic-ai 调用 get_instructions() → L1 能力清单注入 system prompt
  pydantic-ai 调用 get_tools() → 注册 load_travel_planner 工具
  LLM 读到 L1，需要时调用工具获取 L2+L3
```
