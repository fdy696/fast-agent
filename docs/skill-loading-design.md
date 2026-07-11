# Skill 加载机制 — 设计文档

> 版本: 1.0 | 日期: 2026-06-28 | 基于当前项目架构

---

## 1. 整体概览

Skill 系统采用 **L1 → L2 → L3 三级渐进式加载**架构。核心思想是：不在 System Prompt
里塞满所有技能指令，而是先让模型看到一份"目录"（L1），模型判断需要某个能力后主动调用
`load_skill` 工具（L2），按需内联参考模板（L3）。

```
┌─────────────────────────────────────────────────────────────────┐
│                    启动阶段 (startup)                              │
│  SkillRegistry.scan_skills()  扫描 skills/*/SKILL.md             │
│  → 解析 YAML 前页 → 内存注册表 {name: SkillMeta}                 │
│  register_builtin_tools() → load_skill 注册到 ToolRegistry       │
├─────────────────────────────────────────────────────────────────┤
│                    请求阶段 (per-request)                          │
│  ① AgentService.stream()                                        │
│     → 构建 L1 能力清单 → 拼入 system_prompt                       │
│  ② AgentLoop.stream() → PydanticAI agent.run_stream()           │
│  ③ LLM 看到 L1 目录 → 判断需要某个 skill → 调用 load_skill(name) │
│  ④ load_skill tool → 返回 L2 正文 + L3 references（内联在       │
│     tool result 中）→ PydanticAI 自动追加到对话历史               │
│  ⑤ LLM 在下一轮迭代中读到 tool result → 按 skill 指令执行        │
└─────────────────────────────────────────────────────────────────┘
```

### 与 PydanticAI 的架构适配

当前项目使用 PydanticAI 的 `agent.run_stream()` 管理 agent 循环，system prompt 在
`build_agent()` 时一次性设置，中途不可变。参考架构的自定义循环 + 运行时 prompt 重建
方案在此不适用。

**本方案的核心思路**：不依赖运行时重建 system prompt，而是将 L2 内容通过 **tool result**
注入对话历史。PydanticAI 会自动将 tool result 作为 `role: tool` 消息追加，模型在后续
迭代中自然读到 L2 指令。这比参考架构更简洁，消除了 hook 系统和 prompt_builder 模块。

---

## 2. 核心数据结构

### 2.1 SkillMeta — 单个技能的内存表示

```python
# src/agent/skills/loader.py

@dataclass
class SkillMeta:
    name: str                       # 技能名称，如 "travel-planner"
    path: Path                      # 技能目录路径
    description: str                # 一句话描述（来自 YAML 前页）—— 用于 L1 目录
    tools: list[str]                # 该技能关联的工具名（供参考）
    auto_load_references: list[str] # L3 自动加载的参考文件
    body: str                       # 缓存 SKILL.md 正文，避免重复读磁盘
```

### 2.2 SkillRegistry — 全局单例

```python
# src/agent/skills/registry.py

class SkillRegistry:
    skills: dict[str, SkillMeta]   # name → SkillMeta

    # ---------- L1 ----------
    def build_skill_metadata(self) -> str: ...

    # ---------- L2 ----------
    def get_skill_prompt(self, name: str) -> str: ...

    # ---------- L3 ----------
    def get_skill_reference(self, name: str, ref: str) -> str: ...
```

全局实例：`src/agent/skills/__init__.py` → `registry = SkillRegistry()`

---

## 3. Skill 文件结构

### 3.1 YAML 前页 + Markdown 正文

每个 skill 是一个目录，目录名即 skill 名。目录内必须有一个 `SKILL.md`：

```
skills/
└── travel-planner/
    ├── SKILL.md              ← 元数据 + 工作流指令
    └── references/
        └── itinerary-template.md  ← L3 参考模板
```

**SKILL.md 格式**：

```markdown
---
name: travel-planner
description: >
  多日旅游行程规划与攻略。当用户提及"几日游""攻略"等内容时调用。
tools: []
auto_load_references:
  - references/itinerary-template.md
---

## 行程规划

**角色**: 你现在以专业旅行规划师身份回复用户。

### 第一步：确认信息
...

### 第二步：获取数据
...

### 第三步：输出行程
严格按 `references/itinerary-template.md` 结构输出。
```

字段说明：

| 字段 | 必填 | 用途 |
|------|------|------|
| `name` | 是 | skill 唯一标识，与目录名一致 |
| `description` | 是 | L1 目录展示用的一句话描述 |
| `tools` | 否 | 该 skill 关联的工具名列表 |
| `auto_load_references` | 否 | `load_skill` 时自动内联的 L3 文件 |

### 3.2 解析逻辑

```python
# src/agent/skills/loader.py

def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    if not raw.startswith("---"):
        return {}, raw
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw
    _, fm, body = parts
    return yaml.safe_load(fm) or {}, body.strip()
```

### 3.3 目录扫描

```python
# src/agent/skills/loader.py

def scan_skill_dirs(base_dir: Path | None = None) -> dict[str, SkillMeta]:
    """遍历 base_dir 下所有子目录，查找 SKILL.md 并解析。"""
```

启动时调用：`src/core/init_app.py:init_data()` → `registry.scan_skills()`

### 3.4 L3 参考文件

`references/` 目录下的文件使用 `{{placeholder}}` 占位符语法。访问时带路径穿越防护：

```python
def get_skill_reference(self, name: str, ref: str) -> str:
    skill = self.skills[name]
    ref_path = (skill.path / ref).resolve()
    skill_root = skill.path.resolve()
    if not str(ref_path).startswith(str(skill_root)):
        raise ValueError(f"拒绝访问 Skill 目录外的路径: {ref}")
    return ref_path.read_text(encoding="utf-8")
```

---

## 4. 三级加载机制详解

### 4.1 L1 — 可用能力清单（每次请求注入 system prompt）

**构建入口**：`AgentService.stream()` 构建 system prompt 时调用。

```python
# src/agent/skills/registry.py

def build_skill_metadata(self) -> str:
    """生成所有 skill 的 name + description 列表。"""
    if not self.skills:
        return ""
    lines = ["## 可用能力"]
    lines.append("当需要以下能力时，调用 load_skill(name) 获取详细工作流：")
    for skill in self.skills.values():
        lines.append(f"- **{skill.name}**: {skill.description}")
    return "\n".join(lines)
```

**注入位置**：system prompt 末尾追加 L1 目录，模型看到的是一个"能力目录"。

示例注入效果：
```
You are a concise backend AI assistant. ...

## 可用能力
当需要以下能力时，调用 load_skill(name) 获取详细工作流：
- **travel-planner**: 多日旅游行程规划与攻略。当用户提及"几日游"...
```

### 4.2 L2 — 完整工作流指令（模型主动触发加载）

**Step 1**: 模型看到 L1 目录，判断当前任务需要 `travel-planner`，调用工具：

```json
{"name": "load_skill", "arguments": {"name": "travel-planner"}}
```

**Step 2**: `load_skill` 作为内置工具注册在 `ToolRegistry` 中，由 `ToolExecutor.execute()` 执行：

```python
# src/agent/tools/builtin/skill_tool.py  （新增）

def register_skill_tools(registry: ToolRegistry) -> None:
    from agent.skills import registry as skill_registry

    @registry.register(
        name="load_skill",
        description=(
            "加载指定能力的完整工作流指令与操作流程。"
            "仅在判断当前任务确实需要该能力时调用。"
        ),
    )
    def load_skill(name: str) -> dict:
        skill = skill_registry.skills.get(name)
        if not skill:
            return {"error": f"未知能力: {name}"}

        body = skill_registry.get_skill_prompt(name)

        # 内联 L3 auto_load_references
        if skill.auto_load_references:
            parts = [body]
            for ref in skill.auto_load_references:
                try:
                    ref_content = skill_registry.get_skill_reference(name, ref)
                    parts.append(f"\n---\n# 参考: {ref}\n{ref_content}")
                except (ValueError, FileNotFoundError) as exc:
                    parts.append(f"\n<!-- 无法加载参考文件 {ref}: {exc} -->")
            body = "\n".join(parts)

        return {"skill": name, "content": body}
```

**Step 3**: PydanticAI 自动将 tool result 作为对话历史追加。模型在**同一轮 agent 调用**的下一次迭代中读到完整的 L2 指令，然后按 skill 工作流继续执行后续工具调用。

```
Iteration 1: LLM 调用 load_skill("travel-planner")
             ↓ tool result: {"skill": "travel-planner", "content": "## 行程规划\n### 第一步..."}
Iteration 2: LLM 看到完整的 L2 指令 → 按步骤调用 query-weather / bailian_web_search 等
Iteration 3: LLM 汇总工具结果 → 按 L3 模板生成最终回复
```

### 4.3 L3 — 参考模板

L3 参考文件在 `load_skill` 执行时**自动内联**到返回值中（见 Step 2 的 `auto_load_references` 处理），无需模型额外调用。

如果需要按需加载 L3（大型参考文件），可提供独立的 `load_skill_reference` 工具：

```python
@registry.register(
    name="load_skill_reference",
    description="加载指定 skill 的参考模板文件。",
)
def load_skill_reference(skill_name: str, reference: str) -> dict:
    content = skill_registry.get_skill_reference(skill_name, reference)
    return {"skill": skill_name, "reference": reference, "content": content}
```

---

## 5. 整体时序图

```
用户发来消息: "帮我规划一个北京3日游"
    │
    ▼
AgentService.stream()
    ├─ 构建 system_prompt = default_prompt + L1 能力清单
    ├─ 创建 AgentLoop → agent.run_stream(prompt)
    │
    ▼
PydanticAI Agent Loop（最多 settings.AGENT_MAX_STEPS 次迭代）
    │
    ├─ Iteration 1:
    │   system_prompt 含 L1 目录
    │   LLM 判断: 需要 travel-planner 能力
    │   LLM 调用: load_skill("travel-planner")
    │   ToolExecutor 执行 → 返回 L2 正文 + L3 模板
    │   PydanticAI 追加 tool result 到对话历史
    │
    ├─ Iteration 2:
    │   system_prompt 不变（仍含 L1）
    │   对话历史含 load_skill 的 tool result（含 L2 完整指令）
    │   LLM 按 L2 指令第一步: 确认缺失信息 → 回复用户询问
    │   （或如果信息齐全，直接并行调用 MCP 工具）
    │
    ├─ Iteration 3..N:
    │   LLM 继续按 skill 指令执行 → 调用天气/搜索/图片工具
    │   → 汇总结果 → 按 L3 模板格式输出
    │
    └─ 生成最终回复 → 退出循环
```

---

## 6. 伪代码：融入现有 AgentLoop

当前 `AgentLoop.stream()` 不需要修改 —— 它只需在创建 agent 时使用包含 L1 的 system prompt。L2/L3 的注入由 tool 调用机制自然完成。

```python
# src/services/agent_service.py

async def stream(self, payload: ChatRequest, user_id: int) -> AsyncIterator[AgentEvent]:
    ...
    # L1: 在 system prompt 中注入能力清单
    base_prompt = payload.system_prompt or default_system_prompt()
    l1_catalog = registry.build_skill_metadata()  # ← 新增
    if l1_catalog:
        base_prompt = f"{base_prompt}\n\n{l1_catalog}"

    request = AgentLoopRequest(
        prompt=payload.message,
        system_prompt=base_prompt,  # ← 含 L1
        tools=tool_executor.definitions(),  # ← load_skill 已在其中
    )

    async for event in loop.stream(request):
        ...
```

关键点：
- **L1 只在 system prompt 中出现一次**，不做运行时重建
- **L2/L3 通过 tool result 注入对话历史**，PydanticAI 自动处理
- **AgentLoop 零改动** —— 它只关心 `ToolExecutor` 提供的工具列表

---

## 7. 与参考架构的差异对照

| 维度 | 参考文档 | 本项目设计 |
|------|----------|-----------|
| Agent 循环 | 自定义 loop，每迭代重建 prompt | PydanticAI `run_stream()`，不改动 |
| L1 注入方式 | `build_system_prompt()` 占位符替换 | 拼接到 system_prompt 末尾 |
| L2 注入方式 | PostToolUse hook → `active_skills` → 运行时 prompt 重建 | tool result → 对话历史自动追加 |
| L3 注入方式 | hook 激活 `active_refs` → 运行时 prompt 重建 | `load_skill` 返回值内联 |
| Hook 系统 | `hooks.py` + `hooks_travel.py` | **不需要** — 由 PydanticAI 对话机制替代 |
| prompt_builder | 独立模块 `prompt_builder.py` | **不需要** — 无运行时 prompt 重建 |
| 漏触发检测 | `_check_skill_gap()` + counter | 可后续添加，当前通过 L1 prompt 引导即可 |

---

## 8. 涉及的关键文件

| 文件 | 角色 | 状态 |
|------|------|------|
| `src/agent/skills/loader.py` | SKILL.md 扫描 + 解析 + SkillMeta 定义 | ✅ 已有 |
| `src/agent/skills/registry.py` | 注册表：L1/L2/L3 查询方法 | ✅ 已有 |
| `src/agent/skills/__init__.py` | 全局单例 `registry` | ✅ 已有 |
| `src/agent/tools/builtin/skill_tool.py` | `load_skill` 工具注册 | ❌ 待新增 |
| `src/agent/tools/builtin/__init__.py` | 注册 `skill_tool` 到 `register_builtin_tools()` | 待改动 |
| `src/services/agent_service.py` | 构建 system prompt 时注入 L1 | 待改动 |
| `src/core/init_app.py` | 启动时调用 `registry.scan_skills()` | ✅ 已有 |
| `skills/*/SKILL.md` | Skill 定义文件 | 按需添加 |

---

## 9. 扩展新 Skill 的方式

1. 在 `src/agent/skills/` 下创建新目录，如 `skills/my-skill/`
2. 创建 `SKILL.md`，带 YAML 前页（`name`, `description`, `auto_load_references`）
3. （可选）放参考模板在 `references/` 子目录，在 `auto_load_references` 中声明
4. **无需改任何代码** — `scan_skills()` 自动发现，`build_skill_metadata()` 自动纳入 L1，`load_skill` 工具接受任意 `name` 参数
