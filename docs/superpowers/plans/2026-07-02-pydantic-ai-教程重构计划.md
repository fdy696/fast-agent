# Pydantic AI 教程重构计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将《Pydantic AI 从入门到精通》从当前「知识地图」状态重构为一份分层清晰、代码可运行、适合初学者逐层递进的学习教程。

**Architecture:** 本次重构不改变文档的核心定位（Pydantic AI 工程实践），而是通过三个维度修复：1) 修正代码中的工程错误确保可运行性；2) 重新编排章节顺序降低认知负担；3) 统一标注代码类型（可运行/简化/伪代码）并增加学习目标。最终产出为三份独立文档：入门篇、工具篇、工程篇。

**Tech Stack:** Markdown 文档、Python 3.11+、pydantic-ai 2.x

## 全局约束

- 所有文件名与目录名使用中文
- 所有代码示例必须先本地验证可运行再放入文档
- 代码块统一标注类型：`✅ 可直接运行` | `⚠️ 简化示例` | `🧠 原理伪代码` | `🚫 生产不要直接复制`
- 每章开头必须有「本章目标」和「本章你将学到」，结尾必须有「本章练习」和「验收标准」
- 保持 pydantic-ai 2.0 API 兼容，不引入未验证的 API

---

## 总体策略

重构分三轮执行，每轮独立可交付：

**第一轮 (P0)：修复代码正确性错误** — 目标：所有示例代码在复制后可运行或明确标注为伪代码。
**第二轮 (P1)：重新编排章节结构与教学顺序** — 目标：拆分为入门篇、工具篇、工程篇三份文档。
**第三轮 (P2)：表达层优化** — 目标：统一标注、学习闭环、对比表、检查清单。

---

## 第一轮：P0 代码正确性修复

### Task 1: 修复 SkillsToolset 中 `@self.tool` 写法错误

**文件:**
- 修改: `docs/pydantic-ai-comprehensive-guide.md` (第 910-997 行，SkillsToolset 类定义)

**问题:** 第 925-994 行的 `SkillsToolset` 在 class body 中直接使用 `@self.tool` 装饰器。Python 类体执行时没有 `self` 变量，代码无法运行。

**修改方案:** 将 4 个工具（`list_skills`、`load_skill`、`read_skill_resource`、`run_skill_script`）改为在 `_register_tools()` 方法中注册。

- [ ] **Step 1: 修改 SkillsToolset 类定义**

将当前代码：

```python
class SkillsToolset(FunctionToolset):
    """4 个标准工具：list_skills / load_skill / read_skill_resource / run_skill_script"""

    def __init__(self, registry: SkillRegistry, *, auto_reload: bool = False):
        super().__init__()
        self._registry = registry
        self._auto_reload = auto_reload
        self._register_tools()

    async def get_instructions(self, ctx: RunContext) -> str | None:
        if self._auto_reload:
            self._registry.reload()
        skills_xml = self._registry.build_instruction_xml()
        return INSTRUCTIONS_HEADER.format(skills_xml=skills_xml) if skills_xml else None

    # ── L1: 技能目录 ──
    @self.tool
    async def list_skills(_ctx: RunContext) -> dict[str, str]:
        """获取所有可用技能的名称和描述。切换话题时先用此工具查看是否有对口的技能。"""
        return self._registry.list_skills()

    # ── L2: 加载技能 ──
    @self.tool
    async def load_skill(ctx: RunContext, skill_name: str) -> str:
        """加载指定技能的完整指令和资源清单。skill_name 必须与 list_skills 输出完全一致。"""
        ...

    # ── L3: 按需读资源 ──
    @self.tool
    async def read_skill_resource(ctx: RunContext, skill_name: str, resource_name: str) -> str:
        """读取技能的补充资源文件（模板、Schema、参考文档等）。仅在 load_skill 后使用。"""
        ...

    # ── L3: 执行脚本 ──
    @self.tool
    async def run_skill_script(
        ctx: RunContext, skill_name: str, script_name: str,
        args: Annotated[dict[str, Any] | None, BeforeValidator(_json_to_dict)] = None,
    ) -> str:
        """执行技能提供的脚本。脚本名从 load_skill 输出中获取，不要猜测。"""
        ...

    def _register_tools(self) -> None:
        pass  # @self.tool 装饰器在类定义时已自动注册
```

替换为：

```python
class SkillsToolset(FunctionToolset):
    """4 个标准工具：list_skills / load_skill / read_skill_resource / run_skill_script

    ⚠️ 原理演示 — 展示 Agent Skills 渐进式加载的核心思想。
    实际项目建议直接使用 pydantic-ai-skills。
    """

    def __init__(self, registry: SkillRegistry, *, auto_reload: bool = False):
        super().__init__()
        self._registry = registry
        self._auto_reload = auto_reload
        self._register_tools()

    async def get_instructions(self, ctx: RunContext) -> str | None:
        if self._auto_reload:
            self._registry.reload()
        skills_xml = self._registry.build_instruction_xml()
        return INSTRUCTIONS_HEADER.format(skills_xml=skills_xml) if skills_xml else None

    def _register_tools(self) -> None:
        """在 __init__ 中调用，self 可用，安全地注册 4 个工具"""

        # ── L1: 技能目录 ──
        @self.tool
        async def list_skills(ctx: RunContext) -> dict[str, str]:
            """获取所有可用技能的名称和描述。切换话题时先用此工具查看是否有对口的技能。"""
            return self._registry.list_skills()

        # ── L2: 加载技能 ──
        @self.tool
        async def load_skill(ctx: RunContext, skill_name: str) -> str:
            """加载指定技能的完整指令和资源清单。skill_name 必须与 list_skills 输出完全一致。"""
            try:
                skill = self._registry.get_skill(skill_name)
            except KeyError:
                available = ', '.join(self._registry.list_skills().keys()) or '无'
                raise ModelRetry(
                    f"技能 '{skill_name}' 不存在。可用: {available}。"
                    f"请调用 list_skills 确认后重试。"
                )

            resources_xml = '\n'.join(
                f'<resource name="{r.name}"/>' for r in skill.resources
            ) if skill.resources else '<!-- 无资源 -->'

            refs = ''
            if skill.auto_load_references:
                ref_parts = []
                for ref_name in skill.auto_load_references:
                    try:
                        ref_parts.append(self._registry.get_resource(skill_name, ref_name))
                    except Exception:
                        ref_parts.append(f'<!-- 自动加载失败: {ref_name} -->')
                refs = '\n<auto_loaded_references>\n' + '\n\n'.join(ref_parts) + '\n</auto_loaded_references>'

            return LOAD_SKILL_TEMPLATE.format(
                name=skill.name, description=skill.description,
                resources_xml=resources_xml, body=skill.body,
                refs_section=refs,
            )

        # ── L3: 按需读资源 ──
        @self.tool
        async def read_skill_resource(ctx: RunContext, skill_name: str, resource_name: str) -> str:
            """读取技能的补充资源文件（模板、Schema、参考文档等）。仅在 load_skill 后使用。"""
            try:
                return self._registry.get_resource(skill_name, resource_name)
            except KeyError:
                skill = self._registry.get_skill(skill_name)
                available = [r.name for r in skill.resources]
                raise ModelRetry(f"资源 '{resource_name}' 不存在。可用: {available}")

        # ── L3: 执行脚本 ──
        @self.tool
        async def run_skill_script(
            ctx: RunContext, skill_name: str, script_name: str,
            args: Annotated[dict[str, Any] | None, BeforeValidator(_json_to_dict)] = None,
        ) -> str:
            """执行技能提供的脚本。脚本名从 load_skill 输出中获取，不要猜测。

            🧠 本节为原理演示，run_skill_script 只读取脚本源码，并未真正执行。
            生产环境中需要实现子进程执行、超时控制、路径安全校验。
            """
            try:
                skill = self._registry.get_skill(skill_name)
            except KeyError:
                available = ', '.join(self._registry.list_skills().keys()) or '无'
                raise ModelRetry(f"技能 '{skill_name}' 不存在。可用: {available}")

            script = next((s for s in skill.scripts if s.name == script_name), None)
            if script is None:
                available = [s.name for s in skill.scripts]
                raise ModelRetry(f"脚本 '{script_name}' 不存在。可用: {available}")

            # 🧠 原理演示：只读源码，不执行
            return script.path.read_text(encoding='utf-8')
```

- [ ] **Step 2: 验证修改后代码无 `@self.tool` 在 class body 中的情况**

运行: `grep -n "@self.tool" docs/pydantic-ai-comprehensive-guide.md`
预期: 所有 `@self.tool` 出现在 `def _register_tools` 或 `def _register_api_tool` 方法体内部（缩进 >= 8 空格），不在 class 直系 body 中（缩进 4 空格）。

- [ ] **Step 3: 提交**

```bash
git add docs/pydantic-ai-comprehensive-guide.md
git commit -m "fix: 修复 SkillsToolset @self.tool 在 class body 中的错误写法，移入 _register_tools()"
```

---

### Task 2: 修复 `_discover_scripts()` 脚本发现逻辑

**文件:**
- 修改: `docs/pydantic-ai-comprehensive-guide.md` (第 792-816 行，`_discover_scripts` 函数)

**问题:** `_discover_scripts()` 使用 `_RESOURCE_EXTENSIONS = {'.md', '.json', '.yaml', '.yml', '.csv', '.xml', '.txt'}` 过滤脚本文件，导致 `.py`、`.sh`、`.js`、`.ts` 等可执行脚本无法被发现。示例中的 `scripts/search_poi.py` 永远不会被扫描到。

- [ ] **Step 1: 添加 SCRIPT_EXTENSIONS 常量**

在第 732 行 `_RESOURCE_EXTENSIONS` 定义后新增：

```python
_RESOURCE_EXTENSIONS = {'.md', '.json', '.yaml', '.yml', '.csv', '.xml', '.txt'}
_SCRIPT_EXTENSIONS  = {'.py', '.sh', '.js', '.ts'}   # 可执行脚本扩展名
```

- [ ] **Step 2: 重写 `_discover_scripts()` 函数**

将当前第 792-816 行替换为：

```python
def _discover_scripts(skill_dir: Path) -> list[SkillScript]:
    """扫描根目录 + scripts/ 子目录下的可执行脚本

    规则：
    1. 扩展名在 _SCRIPT_EXTENSIONS 中
    2. 无扩展名但具有执行权限的文件也纳入
    3. 排除 __init__.py 和 SKILL.md
    """
    scripts: list[SkillScript] = []
    skill_dir_resolved = skill_dir.resolve()

    # 先检查 scripts/ 子目录
    scripts_dir = skill_dir / 'scripts'
    scan_dirs = [skill_dir]
    if scripts_dir.is_dir():
        scan_dirs.append(scripts_dir)

    for d in scan_dirs:
        for f in d.iterdir():
            if not f.is_file():
                continue
            name = f.name
            if name in {'__init__.py', 'SKILL.md'}:
                continue

            # 可执行：扩展名在 _SCRIPT_EXTENSIONS 中，或无扩展名但可执行
            is_executable = (
                f.suffix.lower() in _SCRIPT_EXTENSIONS
                or ('.' not in name and f.stat().st_mode & 0o111)
            )
            if not is_executable:
                continue

            resolved = f.resolve()
            try:
                resolved.relative_to(skill_dir_resolved)
            except ValueError:
                warnings.warn(f"脚本 {f} 路径穿越，已跳过")
                continue

            scripts.append(SkillScript(
                name=f.relative_to(skill_dir).as_posix(),
                path=resolved,
            ))
    return scripts
```

- [ ] **Step 3: 提交**

```bash
git add docs/pydantic-ai-comprehensive-guide.md
git commit -m "fix: 修复 _discover_scripts 使用错误扩展名集合，拆分为 RESOURCE_EXTENSIONS 和 SCRIPT_EXTENSIONS"
```

---

### Task 3: 修复 `SkillsCapability` 多目录扫描覆盖 bug

**文件:**
- 修改: `docs/pydantic-ai-comprehensive-guide.md` (第 1018-1023 行，`SkillsCapability.__post_init__`)

**问题:** `__post_init__` 循环中对每个目录分别调用 `self._registry.scan()`，每次 `scan()` 内部 `self._skills = scan_skill_dirs(self._base_dir)` 是全量替换，导致只有最后一个目录的 skills 被保留。

- [ ] **Step 1: 修改 SkillRegistry 支持多目录合并**

修改 `SkillRegistry` 类，增加接受多目录的构造和扫描方法：

```python
class SkillRegistry:
    """内存注册表 — 多目录合并 + 原子替换 + lazy load"""

    def __init__(self, base_dirs: list[Path] | None = None):
        self._base_dirs: list[Path] = base_dirs or []
        self._skills: dict[str, SkillMeta] = {}

    def scan(self) -> None:
        """扫描所有 base_dir，合并结果。同名技能后扫描的覆盖先扫描的。"""
        new_skills: dict[str, SkillMeta] = {}
        for base_dir in self._base_dirs:
            discovered = scan_skill_dirs(base_dir)
            new_skills.update(discovered)  # 后扫描覆盖先扫描
        self._skills = new_skills

    def reload(self) -> None:
        """原子替换：先构建新 dict 再赋值，避免并发读到半空状态"""
        self.scan()

    # list_skills / get_skill / get_resource / build_instruction_xml 保持不变
    ...
```

- [ ] **Step 2: 修改 `SkillsCapability.__post_init__`**

将当前代码：

```python
def __post_init__(self) -> None:
    self._registry = SkillRegistry()
    for d in self.directories:
        self._registry._base_dir = Path(d)
        self._registry.scan()
    self._toolset = SkillsToolset(self._registry, auto_reload=self.auto_reload)
```

替换为：

```python
def __post_init__(self) -> None:
    self._registry = SkillRegistry(
        base_dirs=[Path(d) for d in self.directories]
    )
    self._registry.scan()  # 一次性扫描全部目录并合并
    self._toolset = SkillsToolset(self._registry, auto_reload=self.auto_reload)
```

- [ ] **Step 3: 提交**

```bash
git add docs/pydantic-ai-comprehensive-guide.md
git commit -m "fix: 修复 SkillsCapability 多目录扫描覆盖 bug，SkillRegistry 改为接受多 base_dir 合并"
```

---

### Task 4: 删除 `eval()` 示例，替换为安全实现

**文件:**
- 修改: `docs/pydantic-ai-comprehensive-guide.md` (第 134-144 行，`calculate` 工具)

**问题:** 教程中的 `calculate` 工具使用 `eval(expression)` 执行用户输入。即使是教学示例，也不应该展示不安全的模式。

- [ ] **Step 1: 替换为安全计算器实现**

将当前第 134-144 行：

```python
@agent.tool
async def calculate(ctx: RunContext, expression: str) -> str:
    """计算数学表达式。

    Args:
        expression: 数学表达式，例如 "2 + 3 * 4"。
    """
    try:
        return str(eval(expression))
    except Exception as e:
        return f"计算错误: {e}"
```

替换为：

```python
import ast
import operator as op

# 安全的数学计算器 — 白名单方式解析表达式
_SAFE_OPS = {
    ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul,
    ast.Div: op.truediv, ast.Pow: op.pow, ast.USub: op.neg,
}

def _safe_eval(expr: str) -> float:
    """安全地计算四则运算表达式。仅支持数字、+、-、*、/、**、()、空格。"""
    def _eval_node(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_OPS:
            return _SAFE_OPS[type(node.op)](_eval_node(node.operand))
        if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_OPS:
            return _SAFE_OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
        raise ValueError(f"不支持的运算: {ast.dump(node)}")
    return _eval_node(ast.parse(expr.strip(), mode='eval').body)

@agent.tool
async def calculate(ctx: RunContext, expression: str) -> str:
    """计算数学表达式。仅支持四则运算和括号。

    Args:
        expression: 数学表达式，例如 "2 + 3 * 4" 或 "(1 + 2) * 3"。
    """
    try:
        return str(_safe_eval(expression))
    except Exception as e:
        return f"计算错误: {e}"
```

- [ ] **Step 2: 提交**

```bash
git add docs/pydantic-ai-comprehensive-guide.md
git commit -m "fix: 删除 eval() 示例，替换为 ast 白名单安全计算器"
```

---

### Task 5: 统一标注第 5 章代码类型

**文件:**
- 修改: `docs/pydantic-ai-comprehensive-guide.md` (第 5 章全文)

**问题:** 第 5 章是整份文档代码风险最高的部分。`SkillsToolset`、`SkillsCapability`、`run_skill_script` 等实现是原理演示而非可运行代码，但没有明确标注，读者复制后运行会失败。

- [ ] **Step 1: 在第 5 章标题下添加全局标注**

在第 559 行 `## 5. Agent Skills：渐进式能力加载` 下方新增：

```markdown
> 🧠 **本章代码类型说明**
>
> 本章所有代码分为两类：
> - **Layer 1-4**（解析、类型、发现、注册表）：✅ 可直接运行
> - **Layer 5-6**（SkillsToolset、SkillsCapability）：🧠 原理演示，展示渐进式加载的核心思路
>
> 实际项目建议直接使用 `pydantic-ai-skills` 包，无需手写这些实现。
> 如果你在阅读时想运行代码，请只运行 Layer 1-4 并单独测试。
```

- [ ] **Step 2: 在 SkillsToolset 类上方添加行内标注**

在第 910 行 `class SkillsToolset(FunctionToolset):` 上方新增：

```markdown
> 🧠 **本节为原理演示，不是完整可运行实现。**
> 它展示的是 `pydantic-ai-skills` 内部如何用 FunctionToolset + 4 个标准工具
> 实现渐进式加载。实际项目直接 `pip install pydantic-ai-skills` 即可。
```

- [ ] **Step 3: 提交**

```bash
git add docs/pydantic-ai-comprehensive-guide.md
git commit -m "docs: 统一标注第 5 章代码类型，区分可运行代码与原理演示"
```

---

### Task 6: 修复 `KW_ONLY` 未导入

**文件:**
- 修改: `docs/pydantic-ai-comprehensive-guide.md` (第 5 章和第 6 章 import 区域)

**问题:** 第 1012 行和第 1189 行使用了 `_: KW_ONLY` 语法，但文件头部未导入 `KW_ONLY`。Python 3.10+ 中 `KW_ONLY` 需要从 `dataclasses` 导入。

- [ ] **Step 1: 检查所有 import 区域**

第 5 章的 SkillsCapability（第 1004 行附近）和第 6 章的 AppCapability（第 1187 行附近）都需要 `KW_ONLY`。

- [ ] **Step 2: 在第 5 章 import 区域添加 KW_ONLY**

在第 631 行 `from dataclasses import dataclass, field` 改为：

```python
from dataclasses import KW_ONLY, dataclass, field
```

- [ ] **Step 3: 在第 6 章 import 区域添加 KW_ONLY**

在第 1180 行 `from dataclasses import KW_ONLY, dataclass, field` 改为：

```python
from dataclasses import KW_ONLY, dataclass, field
```

- [ ] **Step 4: 验证**

运行: `grep -n "from dataclasses import" docs/pydantic-ai-comprehensive-guide.md`
预期: 所有包含 `KW_ONLY` 使用的章节的 import 行都包含 `KW_ONLY`。

- [ ] **Step 5: 提交**

```bash
git add docs/pydantic-ai-comprehensive-guide.md
git commit -m "fix: 添加 KW_ONLY 到 dataclasses 导入，修复第 5/6 章 NameError"
```

---

## 第二轮：P1 章节重排与结构调整

### Task 7: 拆分为三份独立文档

**文件:**
- 创建: `docs/教程/入门篇-第一个Agent.md`
- 创建: `docs/教程/工具篇-工具调用与状态管理.md`
- 创建: `docs/教程/工程篇-生产级Agent系统.md`

**方案:** 将当前一份 1486 行的巨型文档拆为三份，每份可独立阅读。同时调整章节顺序。

- [ ] **Step 1: 创建目录结构**

```bash
mkdir -p docs/教程
```

- [ ] **Step 2: 创建「入门篇-第一个Agent.md」**

内容从原文档提取并调整顺序：

```markdown
# Pydantic AI 入门篇：第一个 Agent

> ✅ 本文档所有代码可直接运行。面向零基础学习者。

## 本章你将学到
- 创建一个能回复中文的 Agent
- 使用 agent.run() 进行非流式调用
- 使用 agent.run_stream() 实现打字效果
- 理解 model、instructions、user prompt 的关系
- 识别并避免最常见的 3 个坑

---

## 1. Pydantic AI 是什么
（简要介绍：Pydantic AI 是一个基于 Pydantic 的 LLM Agent 框架...）

## 2. 第一个非流式 Agent（先从这里开始）
（agent.run() 最小示例）

## 3. 流式输出 run_stream
（从原第 1 章提取，但先讲 run() 再升级到 run_stream()）

## 4. instructions、user prompt、model 的关系
（概念解释 + 示例）

## 5. 常见坑与调试
（保留原 1.3 内容，补充 debug 技巧）

## 本章练习
1. 把回复语言改成英文
2. 把模型换成 gpt-4o-mini
3. 让 Agent 每次回答不超过 50 字

## 验收标准
你能不看文档写出一个可流式输出的最小 Agent。
```

- [ ] **Step 3: 创建「工具篇-工具调用与状态管理.md」**

内容编排：

```markdown
# Pydantic AI 工具篇：工具调用与状态管理

> ✅ 本文档所有代码可直接运行。面向已能创建基本 Agent 的学习者。

## 前置要求
已完成入门篇，能创建基本 Agent。

## 本章你将学到
- 用 @agent.tool 注册带 RunContext 的工具
- 用 @agent.tool_plain 注册纯函数工具
- 理解工具调用的完整流程
- 管理多轮对话的消息历史
- 使用 UsageLimits 控制成本

---

## 1. 什么是 Tool
（工具调用流程图解）

## 2. @agent.tool：带 RunContext 的工具
（从原 2.1 提取，删除 eval 示例，替换为安全计算器）

## 3. @agent.tool_plain：纯函数工具
（原 2.2）

## 4. 工具参数 schema 与 docstring 最佳实践
（原 2.5 扩展）

## 5. retries 与 ModelRetry
（原 2.5 坑 3 + 第 5 章 ModelRetry 原理）

## 6. Agent 为什么默认无状态
（说明每次 run() 是独立的）

## 7. message_history：实现多轮对话
（原 4.1 + 4.2，主示例改为 result.new_messages()）

## 8. result.new_messages() 的正确用法
（强调不要手拼 ModelMessage）

## 9. Loop Agent：自动续跑
（原 4.3）

## 10. UsageLimits：成本控制
（原 4.4 坑 3 扩展）

## 本章练习
1. 写一个带天气查询 tool 的 Agent
2. 实现一个记住上下文的命令行聊天程序
3. 设置 token 上限并测试超限续跑

## 验收标准
你能从零写出一个带工具调用和多轮对话的 Agent。
```

- [ ] **Step 4: 创建「工程篇-生产级Agent系统.md」**

内容编排（MCP、Skills、Capability 后移到此处）：

```markdown
# Pydantic AI 工程篇：生产级 Agent 系统

> 🧠 本文档包含原理演示代码和生产架构草图。每节开头标注代码类型。
> 面向中高级开发者，需完成工具篇。

## 前置要求
已完成工具篇，理解 Agent、Tool、message_history、ModelRetry。

## 本章你将学到
- 用 FunctionToolset 管理动态工具
- 用 MCPToolset 集成远程工具
- 理解 Agent Skills 渐进式加载思想
- 用 AbstractCapability 统一横切关注点
- 搭建生产级 Agent 项目结构

---

## 1. 为什么需要 Toolset
（普通 tool vs FunctionToolset 对比，原 2.3 精简 + 选择策略）

## 2. FunctionToolset 实战
（原 2.3 DatabaseToolset 示例，保持可运行）

## 3. MCPToolset：远程工具集成
（原第 3 章）

## 4. Agent Skills：渐进式能力加载
（原第 5 章核心思想 + pydantic-ai-skills 用法 + 手写实现原理）
> 🧠 手写 SkillsToolset 为原理演示

## 5. Capability 与 Hooks：统一横切
（原第 6 章精简版，不列 30 个钩子，只讲最常用的 6 个）

## 6. 生产环境项目结构
（原第 7 章修复重复注册后精简 + 架构图）

## 7. 速查表与对比
（原 7.3 速查表 + 新增 Tool/Toolset/MCP/Skills/Capability 对比表）

## 8. 安全与测试清单
（新增：安全检查项、测试类型清单）

## 本章练习
1. 用 MCPToolset 接入一个真实 MCP 服务器
2. 创建一个包含 SKILL.md 的自定义技能
3. 为 Agent 添加日志和告警 capability

## 验收标准
你能设计并实现一个集成 MCP + Skills + 日志告警的生产级 Agent。
```

- [ ] **Step 5: 删除原文件或改为索引**

将原 `pydantic-ai-comprehensive-guide.md` 重命名为 `pydantic-ai-comprehensive-guide-v1-已废弃.md`，并创建新的索引文件 `docs/教程/README.md`：

```markdown
# Pydantic AI 工程学习教程

本教程分三篇，建议按顺序阅读：

1. **[入门篇：第一个 Agent](入门篇-第一个Agent.md)** — 创建能跑起来的 Agent
2. **[工具篇：工具调用与状态管理](工具篇-工具调用与状态管理.md)** — 让 Agent 调用外部能力
3. **[工程篇：生产级 Agent 系统](工程篇-生产级Agent系统.md)** — 搭建可扩展的 Agent 架构

## 学习路径

入门篇（30分钟） → 工具篇（60分钟） → 工程篇（90分钟）

## 旧版文档

原《Pydantic AI 从入门到精通》单文件版本已移至 `docs/superpowers/archive/`。
```

- [ ] **Step 6: 提交**

```bash
git add docs/教程/
git add docs/pydantic-ai-comprehensive-guide.md  # 重命名后的旧文件
git commit -m "docs: 拆分为入门篇/工具篇/工程篇三份独立文档，重排章节顺序"
```

---

### Task 8: 修复第 4 章（工具篇-第 7 节）消息历史为 `result.new_messages()`

**文件:**
- 修改: `docs/教程/工具篇-工具调用与状态管理.md`

**问题:** 原第 4.2 节主示例手拼 `ModelRequest` 和 `ModelResponse`，而 4.4 节「坑 2」又警告不要手拼。主示例和最佳实践建议必须一致。

- [ ] **Step 1: 修改多轮对话主示例**

将原第 4.2 节的 `Conversation.chat()` 方法改为使用 `result.new_messages()`：

```python
@dataclass
class Conversation:
    """管理多轮对话状态"""
    agent: Agent
    messages: list[ModelMessage] = field(default_factory=list)

    async def chat(self, user_input: str) -> str:
        """发送消息，返回完整回复文本"""
        full_reply = ""

        async with self.agent.run_stream(
            user_input,
            message_history=self.messages,
        ) as result:
            async for event in result.stream():
                if hasattr(event, 'delta') and event.delta:
                    print(event.delta, end='', flush=True)
                    full_reply += event.delta

        # ✅ 正确：用框架保证格式正确的 new_messages()
        self.messages.extend(result.new_messages())
        print()
        return full_reply
```

同时删除手拼消息的说明：

> ~~`self.messages.append(ModelRequest(parts=[user_input]))`~~
> ~~`self.messages.append(ModelResponse(parts=[full_reply]))`~~

替换为注释：

```python
# result.new_messages() 包含了本轮的 ModelRequest 和 ModelResponse
# 格式由框架保证，不需要手动构造
```

- [ ] **Step 2: 删除 ModelRequest/ModelResponse 的 import**

如果不再使用 `ModelRequest` 和 `ModelResponse`（只在手拼消息时用到），从示例 import 中删除它们：

```python
from pydantic_ai.messages import ModelMessage  # 只保留 ModelMessage 类型注解
```

- [ ] **Step 3: 提交**

```bash
git add docs/教程/工具篇-工具调用与状态管理.md
git commit -m "fix: 多轮对话主示例统一使用 result.new_messages()，删除手拼消息写法"
```

---

### Task 9: 修复第 7 章（工程篇-第 6 节）重复注册问题

**文件:**
- 修改: `docs/教程/工程篇-生产级Agent系统.md`

**问题:** `capabilities=[app_capability, skills_capability]` 同时 `app_capability.get_toolset()` 返回 `skills_capability.get_toolset()`，导致 SkillsToolset 被注册两次。

- [ ] **Step 1: 修改生产级 Agent 创建代码**

将创建逻辑改为：

```python
def create_production_agent(
    model,
    mcp_servers: list[dict],
    skill_dirs: list[str],
    feishu_webhook: str | None = None,
) -> Agent:
    """创建生产级 Agent，统一注册所有工具和横切关注点"""

    # 1. 创建 MCP toolsets
    mcp_toolsets = []
    for server in mcp_servers:
        transport = StreamableHttpTransport(
            url=server['url'],
            headers={"Authorization": f"Bearer {server['api_key']}"},
        )
        mcp_toolsets.append(MCPToolset(
            transport,
            tool_error_behavior='retry',
            max_retries=3,
            cache_tools=True,
        ))

    # 2. 创建 SkillsCapability
    skills_capability = SkillsCapability(
        directories=skill_dirs,
        auto_reload=True,
    )

    # 3. 创建 AppCapability（横切关注点），不再聚合 SkillsCapability
    app_capability = AppCapability(
        feishu_webhook=feishu_webhook,
        daily_budget=50.0,
    )
    app_capability._mcp_toolsets = mcp_toolsets
    # ✅ 不再设置 app_capability._skill_capability

    # 4. 创建 Agent — 各自独立注册，不会重复
    agent = Agent(
        model=model,
        capabilities=[
            app_capability,       # 日志/告警/成本统计
            skills_capability,    # Agent Skills 工具
        ],
        toolsets=mcp_toolsets,    # MCP 远程工具
        instructions="You are a helpful assistant. Reply in Chinese.",
        retries=2,
        tool_timeout=30.0,
    )

    return agent
```

- [ ] **Step 2: 同步修改 AppCapability 类，删除对 SkillsCapability 的聚合**

删除 `AppCapability` 中的 `_skill_capability` 字段，并将 `get_toolset()` 改为返回 `None`：

```python
@dataclass
class AppCapability(AbstractCapability):
    """生产环境的统一横切层（日志、告警、成本统计）"""
    _: KW_ONLY
    feishu_webhook: str | None = None
    daily_budget: float = 50.0

    _mcp_toolsets: list = field(default_factory=list, init=False)
    _total_cost: float = field(default=0.0, init=False)

    # get_toolset 不再返回 SkillsToolset
    # Skills 通过独立的 SkillsCapability 注册，避免重复
    ...
```

- [ ] **Step 3: 提交**

```bash
git add docs/教程/工程篇-生产级Agent系统.md
git commit -m "fix: 消除 SkillsToolset 重复注册，AppCapability 不再聚合 SkillsCapability"
```

---

### Task 10: 每章增加学习目标、练习和验收标准

**文件:**
- 修改: `docs/教程/入门篇-第一个Agent.md`
- 修改: `docs/教程/工具篇-工具调用与状态管理.md`
- 修改: `docs/教程/工程篇-生产级Agent系统.md`

**问题:** 当前文档只有知识点罗列，缺少教学闭环。

- [ ] **Step 1: 为入门篇每章补充学习闭环**

在入门篇每章开头统一添加：

```markdown
> **本章目标**：（一句话）
> **预计时间**：X 分钟
```

每章结尾统一添加：

```markdown
## 本章练习
1. ...
2. ...

## 验收标准
- [ ] 你能...
- [ ] 你能...

## 常见错误
（从原文档「常见坑」提取，精简为 2-3 条）
```

- [ ] **Step 2: 为工具篇每章补充学习闭环**

同上格式。

- [ ] **Step 3: 为工程篇每章补充学习闭环**

工程篇额外增加：

```markdown
## 延伸阅读
- pydantic-ai 官方文档：...
- Agent Skills 规范：...
```

- [ ] **Step 4: 提交**

```bash
git add docs/教程/
git commit -m "docs: 每章增加学习目标、练习、验收标准和常见错误"
```

---

## 第三轮：P2 表达层优化

### Task 11: 标题改为「工程实践路线图」，增加对比表和检查清单

**文件:**
- 修改: `docs/教程/README.md`

- [ ] **Step 1: 调整 README 标题和定位**

将索引文件标题从"Pydantic AI 工程学习教程"细化为：

```markdown
# Pydantic AI 工程实践路线图

> 从入门到工程落地，分层递进的 Pydantic AI 学习指南。
>
> **定位**：不是 API 文档的翻译，而是带工程判断的实践路线图。
> 每个概念都回答三个问题：什么时候用？怎么用？有什么坑？
```

- [ ] **Step 2: 新增 Tool/Toolset/MCP/Skills/Capability 对比表**

在 README 中新增：

```markdown
## 能力注册方式对比

| 方式 | 适用场景 | 注册时机 | 工具数量 | 难度 |
|------|---------|---------|---------|------|
| `@agent.tool` | 编译时已知的工具 | 代码加载时 | 单个 | ⭐ |
| `@agent.tool_plain` | 纯函数，不依赖 Agent 状态 | 代码加载时 | 单个 | ⭐ |
| `FunctionToolset` | 运行时从 DB/文件动态发现的工具 | `__init__` 时 | 多个 | ⭐⭐ |
| `MCPToolset` | 远程 MCP 服务器上的工具 | `__aenter__` 时 | 多个 | ⭐⭐⭐ |
| `Agent Skills` | 领域知识渐进式加载 | 按需 (L0→L1→L2→L3) | 多个 | ⭐⭐⭐ |
| `AbstractCapability` | 横切关注点（日志/告警/cost） | Agent 创建时 | 0~多个 | ⭐⭐⭐⭐ |
```

- [ ] **Step 3: 新增安全清单和测试清单**

```markdown
## 安全清单

- [ ] 工具函数不使用 `eval()` 或 `exec()` 处理用户输入
- [ ] MCP 连接使用 HTTPS + Token 认证
- [ ] 脚本执行限制在沙箱目录内（路径穿越检查）
- [ ] `UsageLimits` 设置合理的 token 上限
- [ ] 人工审批工具标记 `requires_approval=True`
- [ ] 工具超时设置 `tool_timeout`

## 测试清单

- [ ] 每个 tool 有独立的单元测试
- [ ] Mock LLM 响应进行 Agent 行为测试
- [ ] MCP 连接失败有降级策略测试
- [ ] 超长对话自动续跑测试
- [ ] 多轮对话状态恢复测试
```

- [ ] **Step 4: 提交**

```bash
git add docs/教程/README.md
git commit -m "docs: 优化定位说明，新增对比表、安全清单和测试清单"
```

---

### Task 12: 精简第 6 章 Capability 钩子列表

**文件:**
- 修改: `docs/教程/工程篇-生产级Agent系统.md` (原第 6 章内容)

**问题:** 原文档列出大量可能未经版本验证的钩子，容易误导读者。

- [ ] **Step 1: 将钩子列表替换为「最常用的 6 个钩子」**

```markdown
## 常用生命周期钩子

AbstractCapability 提供丰富的钩子。以下是生产环境最常用的 6 个：

| 钩子 | 时机 | 典型用途 |
|------|------|---------|
| `before_run` | Agent run 开始前 | 初始化资源、建立 MCP 连接 |
| `after_run` | Agent run 结束后 | 持久化 cost、清理资源 |
| `before_tool_execute` | 工具执行前 | 审计日志、参数校验 |
| `after_tool_execute` | 工具执行后 | 告警、更新 metrics |
| `on_tool_execute_error` | 工具出错时 | 统一重试决策 |
| `after_model_request` | LLM 调用后 | Token 消耗监控 |

> ⚠️ 更多钩子参考 pydantic-ai 官方文档。本文仅列出经过版本验证的常用钩子。
```

- [ ] **Step 2: 删除未验证的钩子列表**

删除原 6.2 节中的 30 个钩子列表（`before_node_run`、`after_node_run`、`before_tool_validate`、`wrap_model_request`、`wrap_run_event_stream` 等），替换为注释：

```markdown
<!-- 完整钩子列表请查阅 pydantic-ai 官方文档 -->
```

- [ ] **Step 3: 提交**

```bash
git add docs/教程/工程篇-生产级Agent系统.md
git commit -m "docs: 精简 Capability 钩子列表为最常用的 6 个，避免未验证 API 误导"
```

---

### Task 13: 代码块统一标注

**文件:**
- 修改: `docs/教程/入门篇-第一个Agent.md`
- 修改: `docs/教程/工具篇-工具调用与状态管理.md`
- 修改: `docs/教程/工程篇-生产级Agent系统.md`

**问题:** 三份文档中哪些代码可运行、哪些是伪代码，需要统一的视觉标注。

- [ ] **Step 1: 定义标注规范**

在 README 中添加：

```markdown
## 代码标注说明

本文档中所有代码块使用以下标注：

| 标注 | 含义 |
|------|------|
| ✅ 可直接运行 | 复制后即可运行，已通过本地验证 |
| ⚠️ 简化示例 | 省略了错误处理/配置等，需补充后才能运行 |
| 🧠 原理演示 | 展示实现思路，不是完整可运行代码 |
| 🚫 生产不要复制 | 有安全隐患或设计缺陷，仅供学习参考 |
```

- [ ] **Step 2: 为三份文档的所有代码块添加标注**

使用标注规则：
- 入门篇所有代码 → ✅ 可直接运行
- 工具篇 90% 代码 → ✅ 可直接运行
- 工程篇 MCP 示例 → ✅ 可直接运行（需要真实的 MCP 服务器 URL）
- 工程篇 SkillsToolset 手写实现 → 🧠 原理演示
- 工程篇生产架构代码 → ⚠️ 简化示例

具体修改为在每个代码块开头添加一行注释标注：

```python
# ✅ 可直接运行
import asyncio
from pydantic_ai import Agent
...
```

或对于原理演示：

```python
# 🧠 原理演示 — 展示 Agent Skills 渐进式加载的内部实现思路
# 不可直接运行，实际项目请用 pydantic-ai-skills
class SkillsToolset(FunctionToolset):
    ...
```

- [ ] **Step 3: 提交**

```bash
git add docs/教程/
git commit -m "docs: 所有代码块统一标注可运行/简化/原理/不要复制"
```

---

## 完成检查清单

- [ ] 所有 P0 bug 已修复（Task 1-6）
- [ ] 三份新文档创建完成（Task 7）
- [ ] 章节顺序符合入门→工具→工程的学习路径
- [ ] 消息历史统一使用 `result.new_messages()`（Task 8）
- [ ] 重复注册问题已修复（Task 9）
- [ ] 每章有学习目标、练习、验收标准（Task 10）
- [ ] 标题和定位已优化（Task 11）
- [ ] Capability 钩子列表已精简（Task 12）
- [ ] 代码标注规范已建立（Task 13）
- [ ] 原文档已存档为 v1-已废弃
