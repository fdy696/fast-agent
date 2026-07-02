# Pydantic AI 工程篇：生产级 Agent 系统

> 🧠 本文档包含原理演示代码和生产架构草图。每节开头标注代码类型。
> 面向中高级开发者，需完成工具篇。

## 前置要求
已完成工具篇，理解 Agent、Tool、message_history、ModelRetry。

## 本章你将学到
- 用 `FunctionToolset` 管理动态工具
- 用 `MCPToolset` 集成远程工具
- 理解 Agent Skills 渐进式加载思想
- 用 `AbstractCapability` 统一横切关注点
- 搭建生产级 Agent 项目结构

---

## 1. 为什么需要 Toolset

普通的 `@agent.tool` 只能注册编译时已知的工具。当工具列表来自外部系统（文件、数据库、远程服务）时，需要用 Toolset。

| 特性 | @agent.tool | FunctionToolset | MCPToolset |
|------|-------------|-----------------|------------|
| 编译时确定 | 是 | 否 | 否 |
| 运行时动态加载 | 否 | 是 | 是（从 MCP 服务器）|
| 远程调用 | 否 | 否 | 是 |
| 自动重试 | retries=N | retries=N | tool_error_behavior |
| 使用场景 | 简单工具函数 | DB/文件驱动的工具 | 外部 MCP 服务 |

**选择策略：**

```
工具是在写代码时就知道的？
  ├── 是 → @agent.tool / @agent.tool_plain
  └── 否 → FunctionToolset 子类
       ├── 工具来自外部系统（文件、数据库、MCP）？ → FunctionToolset
       └── 工具来自网络上的 MCP 服务器？ → MCPToolset
```

## 2. FunctionToolset 实战

`FunctionToolset` 子类可以在运行时动态注册工具。示例：从数据库加载 API 端点。

```python
import asyncio
from pydantic_ai import Agent, RunContext
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from openai import AsyncOpenAI


class DatabaseToolset(FunctionToolset):
    """从数据库动态加载的 API 工具集"""

    def __init__(self, api_endpoints: list[dict]):
        super().__init__()
        # 动态注册工具 — 不需要手写 get_tools/call_tool
        for ep in api_endpoints:
            self._register_api_tool(ep)

    def _register_api_tool(self, endpoint: dict):
        name = endpoint['name']
        description = endpoint['description']

        @self.tool(name=name, retries=1)
        async def api_tool(ctx: RunContext, **kwargs) -> str:
            """动态生成的 API 调用工具"""
            return f"调用了 {name}，参数: {kwargs}"

        # 设置工具描述（覆盖函数 docstring）
        api_tool.description = description


# 使用
client = AsyncOpenAI(api_key="your-api-key", base_url="https://api.deepseek.com/v1")
model = OpenAIChatModel("deepseek-chat", provider=OpenAIProvider(openai_client=client))

toolset = DatabaseToolset([
    {"name": "search_users", "description": "搜索用户"},
    {"name": "create_order", "description": "创建订单"},
])
agent = Agent(
    model=model,
    toolsets=[toolset],
    instructions="You are a helpful assistant. Reply in Chinese.",
)


async def main():
    result = await agent.run("帮我搜索用户 '张三'")
    print(result.data)


asyncio.run(main())
```

**关键原理**：`@self.tool` 自动处理 schema 生成、参数验证、工具注册——不需要手写 `ToolDefinition`。`FunctionToolset.get_tools()` 是框架自动调用的，你写工具函数就行。

### 常见坑

**坑 1：忘记 `ctx` 参数**

```python
# ❌ FunctionToolset 中，@self.tool 装饰的函数必须有 ctx
@self.tool
async def my_tool(param: str) -> str:  # 缺少 ctx！
    ...

# ✅
@self.tool
async def my_tool(ctx: RunContext, param: str) -> str:
    ...
```

## 3. MCPToolset：远程工具集成

MCP（Model Context Protocol）允许 Agent 调用远程服务器上的工具，就像调用本地工具一样。

### 3.1 最小可运行代码

```python
from pydantic_ai.mcp import MCPToolset, StreamableHttpTransport
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from openai import AsyncOpenAI
import asyncio

# 1. 创建 MCP transport（连接层）
transport = StreamableHttpTransport(
    url="https://your-mcp-server.example.com/mcp",
    headers={"Authorization": "Bearer your-api-key"},
)

# 2. 创建 MCPToolset（内置重试 + 错误处理）
mcp_toolset = MCPToolset(
    transport,
    tool_error_behavior='retry',  # 工具调用失败自动重试（默认值）
    max_retries=3,
    cache_tools=True,             # 缓存工具列表，避免每次 run 重新获取
    init_timeout=30.0,            # 初始化超时
)

# 3. 使用
client = AsyncOpenAI(api_key="your-api-key", base_url="https://api.deepseek.com/v1")
model = OpenAIChatModel("deepseek-chat", provider=OpenAIProvider(openai_client=client))

agent = Agent(
    model=model,
    toolsets=[mcp_toolset],
    instructions="You are a helpful assistant. Reply in Chinese.",
)


async def main():
    async with agent.run_stream("帮我搜索最近的新闻") as result:
        async for event in result.stream():
            if hasattr(event, 'delta') and event.delta:
                print(event.delta, end='', flush=True)


# 4. 生命周期管理
async def startup():
    await mcp_toolset.__aenter__()  # 建立连接，获取工具列表


async def shutdown():
    await mcp_toolset.__aexit__(None, None, None)  # 关闭连接


asyncio.run(main())
```

### 3.2 发生了什么

```
应用启动:
  MCPToolset.__aenter__()
    ├── HTTP 连接到 MCP 服务器
    ├── 调用 tools/list → 获取远程工具列表
    └── 缓存工具 schema（cache_tools=True）

Agent run:
  LLM 看到 MCP 工具列表（和本地工具混在一起）
  LLM 决定调用 MCP 工具
    ├── MCPToolset.call_tool(name, args)
    │     ├── HTTP 调用 MCP 服务器的 tools/call
    │     ├── 成功 → 返回结果
    │     └── 失败 + tool_error_behavior='retry' → 框架自动重试
    └── 结果注入对话历史
```

### 3.3 最佳实践：用 `process_tool_call` 替代 `call_tool` 覆盖

不要覆盖 `call_tool` 来加告警/日志，用 `process_tool_call` 回调：

```python
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


async def process_tool(
    tool_name: str,
    args: dict[str, Any],
    ctx: RunContext,
    call_next: Callable[[], Awaitable[Any]],
) -> Any:
    """在 MCP tool 调用前后注入逻辑，不破坏框架的重试机制"""
    logger.info("MCP tool: %s(%s)", tool_name, args)
    try:
        result = await call_next()  # 执行真实的 MCP 调用
        logger.info("MCP tool %s 完成", tool_name)
        return result
    except Exception as e:
        logger.error("MCP tool %s 失败: %s", tool_name, e)
        # 可选：发送告警
        await send_alert(f"MCP 工具异常: {tool_name}: {e}")
        raise  # 重新抛出，让框架处理重试


mcp_toolset = MCPToolset(transport, process_tool_call=process_tool)
```

### 3.4 常见坑

**坑 1：覆盖 `call_tool` 破坏重试**

```python
# ❌ 覆盖 call_tool 会绕过框架的 tool_error_behavior
class MyMCPToolset(MCPToolset):
    async def call_tool(self, name, args, ctx, tool):
        # 这里的异常处理会覆盖框架的自动重试
        ...

# ✅ 用 process_tool_call 回调
MCPToolset(transport, process_tool_call=my_callback)
```

**坑 2：忘记管理 MCPToolset 生命周期**

```python
# ❌ 没有 __aenter__，工具列表为空
agent = Agent(model=model, toolsets=[MCPToolset(transport)])
# 运行时报错：工具未初始化

# ✅ 在使用前建立连接
mcp = MCPToolset(transport)
await mcp.__aenter__()
agent = Agent(model=model, toolsets=[mcp])
```

**坑 3：不缓存工具列表**

`cache_tools=True`（默认值）使工具列表在首次获取后缓存。如果你在运行时动态增删 MCP 服务器工具，需要设置为 `cache_tools=False`，否则 Agent 看不到变化。

## 4. Agent Skills：渐进式能力加载

> 🧠 本节包含原理演示代码。`SkillsToolset` 为原理演示，展示渐进式加载的核心思想。
> 实际项目建议直接使用 `pydantic-ai-skills` 包。

### 4.1 为什么需要 Agent Skills

普通的 tool 注册方式有一个问题：你全部注册，LLM 全部看到。当领域指令很长（几百行的攻略模板、多步骤工作流），每一次对话都会被塞进 system prompt，烧 token 且可能干扰 LLM。

Agent Skills 规范的解决思路是**渐进式披露**：

```
L0: 启动时扫描文件系统 → 只记住名称和一句话描述
L1: Agent 调用 list_skills → 看到可用技能目录（~50 tokens）
L2: Agent 调用 load_skill(name) → 拿到完整指令（~1000 tokens）
L3: Agent 调用 read_skill_resource / run_skill_script → 按需用具体资源
```

### 4.2 Skill 目录结构

一个 Skill 就是一个包含 `SKILL.md` 的目录：

```
skills/
└── travel-planner/
    ├── SKILL.md              # 必需：YAML 前页 + Markdown 指令
    ├── references/
    │   └── itinerary-template.md  # 可选：补充资源
    └── scripts/
        └── search_poi.py          # 可选：可执行脚本
```

`SKILL.md` 示例：

```markdown
---
name: travel-planner
description: >
  多日旅游行程规划与攻略。当用户提及"几日游""攻略""行程安排"
  等内容时，必须先加载此技能获取完整工作流。
auto_load_references:
  - references/itinerary-template.md
---

## 行程规划

**角色**: 你以专业旅行规划师身份回复用户。

### 第一步：确认信息
逐一确认目的地、天数、出行日期、出发地。

### 第二步：获取数据
1. 查询目的地天气
2. 搜索旅游攻略
3. 获取景点图片

### 第三步：输出行程
严格按 `references/itinerary-template.md` 结构输出。
```

### 4.3 原理：SkillsToolset（4 个标准工具）

> 🧠 **本节为原理演示**，展示 pydantic-ai-skills 内部如何用 FunctionToolset + 4 个标准工具实现渐进式加载。

```python
"""
Agent Skills 核心实现 — 4 个标准工具实现渐进式加载。
实际项目直接 pip install pydantic-ai-skills 即可。
"""

import re
import warnings
from dataclasses import KW_ONLY, dataclass, field
from pathlib import Path
from typing import Any, Annotated

import yaml
from pydantic import BeforeValidator
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import FunctionToolset

# ═══════════════════════════════════════════════════════
# Layer 1: 解析（纯函数，零依赖）
# ═══════════════════════════════════════════════════════

SKILL_NAME_PATTERN = re.compile(r'^[a-z0-9]+(-[a-z0-9]+)*$')
RESERVED_WORDS = {'anthropic', 'claude'}


def parse_skill_md(content: str) -> tuple[dict, str]:
    """
    逐行扫描 YAML 前页。

    为什么不用 split("---", 2)？
    body 里可能出现 ---（Markdown 水平线），split 会错误截断。
    """
    lines = content.split('\n')
    if not lines or lines[0].rstrip() != '---':
        return {}, content.strip()

    closing_idx = None
    for i in range(1, len(lines)):
        if lines[i].rstrip() == '---':
            closing_idx = i
            break
    if closing_idx is None:
        return {}, content.strip()

    fm_yaml = '\n'.join(lines[1:closing_idx]).strip()
    body = '\n'.join(lines[closing_idx + 1:]).strip()

    if not fm_yaml:
        return {}, body

    try:
        frontmatter = yaml.safe_load(fm_yaml)
    except yaml.YAMLError as e:
        raise ValueError(f'YAML 解析失败: {e}') from e
    if not isinstance(frontmatter, dict):
        raise ValueError(f'前页必须是字典，实际是 {type(frontmatter).__name__}')
    return frontmatter, body


def validate_skill_name(name: str, location: str = '') -> None:
    """校验技能名符合 Agent Skills 规范。不抛异常，只发 UserWarning。"""
    if len(name) > 64:
        warnings.warn(f"技能名 '{name}'{location} 超过 64 字符")
    elif not SKILL_NAME_PATTERN.match(name):
        warnings.warn(f"技能名 '{name}'{location} 应为小写字母、数字、连字符")
    for word in RESERVED_WORDS:
        if word in name.lower():
            warnings.warn(f"技能名 '{name}'{location} 包含保留字 '{word}'")


# ═══════════════════════════════════════════════════════
# Layer 2: 类型
# ═══════════════════════════════════════════════════════

@dataclass
class SkillResource:
    """资源文件 — lazy load，只有 Agent 实际读取时才读磁盘"""
    name: str
    path: Path
    _content: str | None = field(default=None, repr=False)

    @property
    def content(self) -> str:
        if self._content is None:
            self._content = self.path.read_text(encoding='utf-8')
        return self._content


@dataclass
class SkillScript:
    """可执行脚本"""
    name: str
    path: Path
    description: str = ''


@dataclass
class SkillMeta:
    """技能的完整元数据"""
    name: str
    path: Path
    description: str
    body: str
    resources: list[SkillResource] = field(default_factory=list)
    scripts: list[SkillScript] = field(default_factory=list)
    auto_load_references: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════
# Layer 3: 发现（文件扫描 + 安全）
# ═══════════════════════════════════════════════════════

_RESOURCE_EXTENSIONS = {'.md', '.json', '.yaml', '.yml', '.csv', '.xml', '.txt'}
_SCRIPT_EXTENSIONS = {'.py', '.sh', '.js', '.ts'}


def scan_skill_dirs(base_dir: Path) -> dict[str, SkillMeta]:
    """扫描 base_dir 下所有一级子目录的 SKILL.md"""
    skills: dict[str, SkillMeta] = {}
    if not base_dir.is_dir():
        return skills

    for skill_dir in sorted(base_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_file = skill_dir / 'SKILL.md'
        if not skill_file.exists():
            continue

        raw = skill_file.read_text(encoding='utf-8')
        try:
            frontmatter, body = parse_skill_md(raw)
        except ValueError as e:
            warnings.warn(f"跳过无效技能 {skill_dir}: {e}")
            continue

        name = str(frontmatter.get('name', skill_dir.name))

        skills[name] = SkillMeta(
            name=name,
            path=skill_dir.resolve(),
            description=str(frontmatter.get('description', '')),
            body=body.strip(),
            resources=_discover_resources(skill_dir),
            scripts=_discover_scripts(skill_dir),
            auto_load_references=frontmatter.get('auto_load_references', []),
        )
    return skills


def _discover_resources(skill_dir: Path) -> list[SkillResource]:
    """rglob 递归发现资源文件，排除 SKILL.md，symlink 穿越检测"""
    resources: list[SkillResource] = []
    skill_dir_resolved = skill_dir.resolve()
    for ext in _RESOURCE_EXTENSIONS:
        for f in skill_dir.rglob(f'*{ext}'):
            if f.name.upper() == 'SKILL.MD':
                continue
            resolved = f.resolve()
            try:
                resolved.relative_to(skill_dir_resolved)
            except ValueError:
                warnings.warn(f"资源 {f} 路径穿越，已跳过")
                continue
            resources.append(SkillResource(
                name=f.relative_to(skill_dir).as_posix(),
                path=resolved,
            ))
    return resources


def _discover_scripts(skill_dir: Path) -> list[SkillScript]:
    """扫描根目录 + scripts/ 子目录下的可执行脚本"""
    scripts: list[SkillScript] = []
    skill_dir_resolved = skill_dir.resolve()
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
            scripts.append(SkillScript(name=f.relative_to(skill_dir).as_posix(), path=resolved))
    return scripts


# ═══════════════════════════════════════════════════════
# Layer 4: 注册表
# ═══════════════════════════════════════════════════════

class SkillRegistry:
    """内存注册表 — 多目录合并 + 原子替换 + lazy load"""

    def __init__(self, base_dirs: list[Path] | None = None):
        self._base_dirs: list[Path] = base_dirs or []
        self._skills: dict[str, SkillMeta] = {}

    def scan(self) -> None:
        new_skills: dict[str, SkillMeta] = {}
        for base_dir in self._base_dirs:
            discovered = scan_skill_dirs(base_dir)
            new_skills.update(discovered)
        self._skills = new_skills

    def reload(self) -> None:
        """原子替换：先构建新 dict 再赋值，避免并发读到半空状态"""
        self.scan()

    def list_skills(self) -> dict[str, str]:
        return {name: s.description for name, s in self._skills.items()}

    def get_skill(self, name: str) -> SkillMeta:
        if name not in self._skills:
            raise KeyError(f"未知技能: {name}")
        return self._skills[name]

    def get_resource(self, skill_name: str, resource_name: str) -> str:
        skill = self.get_skill(skill_name)
        for r in skill.resources:
            if r.name == resource_name:
                return r.content
        raise KeyError(f"未知资源: {skill_name}/{resource_name}")

    def build_instruction_xml(self) -> str:
        if not self._skills:
            return ''
        lines = ['<available_skills>']
        for skill in sorted(self._skills.values(), key=lambda s: s.name):
            lines.append(f'  <skill>')
            lines.append(f'    <name>{skill.name}</name>')
            lines.append(f'    <description>{skill.description}</description>')
            lines.append(f'  </skill>')
        lines.append('</available_skills>')
        return '\n'.join(lines)


# ═══════════════════════════════════════════════════════
# Layer 5: SkillsToolset（FunctionToolset + 4 标准工具）
# ═══════════════════════════════════════════════════════

LOAD_SKILL_TEMPLATE = """<skill>
<name>{name}</name>
<description>{description}</description>

<resources>
{resources_xml}
</resources>

<instructions>
{body}
</instructions>
{refs_section}
</skill>"""

INSTRUCTIONS_HEADER = """你拥有一组包含领域知识的能力（skills）。

<available_skills>
{skills_xml}
</available_skills>

使用规则：
1. 先调用 load_skill(name) 获取完整指令
2. 仅 load_skill 后，按需调用 read_skill_resource 或 run_skill_script
3. 使用 load_skill 输出中列出的确切资源名和脚本名，不要猜测"""


def _json_to_dict(v: Any) -> Any:
    """LLM 可能传 JSON 字符串而非 dict，自动转换"""
    import json
    if isinstance(v, str):
        parsed = json.loads(v)
        if not isinstance(parsed, dict):
            raise ValueError('参数必须是 JSON 对象')
        return parsed
    return v


class SkillsToolset(FunctionToolset):
    """4 个标准工具：list_skills / load_skill / read_skill_resource / run_skill_script

    🧠 原理演示 — 展示 Agent Skills 渐进式加载的核心思想。
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
        """在 __init__ 中调用，注册 4 个工具"""

        @self.tool
        async def list_skills(ctx: RunContext) -> dict[str, str]:
            """获取所有可用技能的名称和描述。切换话题时先用此工具查看是否有对口的技能。"""
            return self._registry.list_skills()

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

        @self.tool
        async def read_skill_resource(ctx: RunContext, skill_name: str, resource_name: str) -> str:
            """读取技能的补充资源文件（模板、Schema、参考文档等）。仅在 load_skill 后使用。"""
            try:
                return self._registry.get_resource(skill_name, resource_name)
            except KeyError:
                skill = self._registry.get_skill(skill_name)
                available = [r.name for r in skill.resources]
                raise ModelRetry(f"资源 '{resource_name}' 不存在。可用: {available}")

        @self.tool
        async def run_skill_script(
            ctx: RunContext, skill_name: str, script_name: str,
            args: Annotated[dict[str, Any] | None, BeforeValidator(_json_to_dict)] = None,
        ) -> str:
            """执行技能提供的脚本。脚本名从 load_skill 输出中获取，不要猜测。

            🧠 本节为原理演示，run_skill_script 只读取脚本源码，并未真正执行。
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

            return script.path.read_text(encoding='utf-8')


# ═══════════════════════════════════════════════════════
# Layer 6: SkillsCapability（一行式创建）
# ═══════════════════════════════════════════════════════

@dataclass
class SkillsCapability(AbstractCapability):
    """
    一行式创建:
        Agent(capabilities=[SkillsCapability(directories=['./skills'], auto_reload=True)])

    __post_init__ 原子化：构造完成 = 技能已扫描 + toolset 已就绪。
    """
    _: KW_ONLY
    directories: list[str | Path] = field(default_factory=list)
    auto_reload: bool = False
    _toolset: SkillsToolset = field(init=False, repr=False)
    _registry: SkillRegistry = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._registry = SkillRegistry(base_dirs=[Path(d) for d in self.directories])
        self._registry.scan()
        self._toolset = SkillsToolset(self._registry, auto_reload=self.auto_reload)

    def get_toolset(self) -> SkillsToolset:
        return self._toolset

    def get_instructions(self):
        return None  # toolset.get_instructions() 按需提供
```

## 5. Capability 与 Hooks：统一横切

> 本节只讲最常用的 6 个钩子。`AbstractCapability` 提供 30 个生命周期钩子，但多数场景只需其中几个。

### 5.1 为什么需要 Capability

当前典型的工具管理有多个横切关注点——告警、日志、重试、cost 统计——分散在不同位置：

```
make_guarded(current_time)    ← 告警包装
make_guarded(ask_human)       ← 告警包装
AlertingMCPToolset.call_tool  ← 告警 + 重连
SkillToolset                  ← 无告警、无重试  ← 缺口
```

`AbstractCapability` 提供生命周期钩子，把这些横切关注点统一到一个入口。

### 5.2 最常用的 6 个钩子

```python
from pydantic_ai.capabilities import AbstractCapability


class AppCapability(AbstractCapability):
    """生产环境的统一横切层"""

    # ── 1. 工具注册 ──
    def get_toolset(self) -> FunctionToolset | None:
        """返回工具集"""
        ...

    def get_instructions(self):
        """注入 system prompt 指令"""
        ...

    # ── 2. Run 生命周期 ──
    async def before_run(self, ctx, run_context):
        """每个 Agent run 开始前（初始化资源）"""
        ...

    async def after_run(self, ctx, run_context, result, error):
        """每个 Agent run 结束后（清理、持久化 cost）"""
        if error:
            await self._alert("Agent Run 异常", str(error))

    # ── 3. Tool 生命周期 ──
    async def before_tool_execute(self, ctx, tool_name, args):
        """工具执行前（校验参数、记录审计日志）"""
        ...

    async def after_tool_execute(self, ctx, tool_name, args, result, error):
        """工具执行后（告警、更新 metrics）"""
        if error:
            await self._alert(f"Tool '{tool_name}' 异常: {error}")

    async def on_tool_execute_error(self, ctx, tool_name, args, error):
        """决定重试还是放弃"""
        msg = str(error).lower()
        if 'timeout' in msg:
            return 'retry'
        if 'rate limit' in msg:
            return 'retry'
        return 'error'

    # ── 4. Model 生命周期 ──
    async def after_model_request(self, ctx, messages, response, error):
        """每次 LLM 调用后检查 token"""
        if response and hasattr(response, 'usage'):
            tokens = response.usage.total_tokens
            if tokens > 50000:
                await self._alert("大 Token 消耗", f"单次调用 {tokens} tokens")
```

### 5.3 核心用法：统一告警 + Cost 统计

```python
import logging
from dataclasses import KW_ONLY, dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class AppCapability(AbstractCapability):
    """生产环境的统一横切层"""
    _: KW_ONLY
    feishu_webhook: str | None = None
    daily_budget: float = 50.0  # 每日成本预算（美元）

    _mcp_toolsets: list = field(default_factory=list, init=False)
    _skill_capability: SkillsCapability | None = field(default=None, init=False)
    _total_cost: float = field(default=0.0, init=False)

    async def before_run(self, ctx, run_context):
        """Run 开始：初始化资源"""
        logger.info("Agent run 开始")
        for toolset in self._mcp_toolsets:
            if not toolset.is_running:
                await toolset.__aenter__()

    async def after_run(self, ctx, run_context, result, error):
        """Run 结束：持久化统计数据"""
        if error:
            logger.error("Agent run 失败: %s", error)
            await self._alert("Agent Run 异常", str(error))
        if hasattr(run_context, 'usage'):
            cost = self._calculate_cost(run_context.usage)
            self._total_cost += cost
            logger.info("Run cost: $%.4f (累计: $%.2f)", cost, self._total_cost)
            if self._total_cost > self.daily_budget:
                await self._alert("Cost 超预算", f"累计 ${self._total_cost:.2f}")

    async def before_tool_execute(self, ctx, tool_name, args):
        """工具执行前：审计日志"""
        logger.debug("Tool: %s(%s)", tool_name, args)

    async def after_tool_execute(self, ctx, tool_name, args, result, error):
        """工具执行后：告警"""
        if error:
            logger.error("Tool %s 失败: %s", tool_name, error)
            await self._alert(f"Tool 异常: {tool_name}", str(error))

    async def on_tool_execute_error(self, ctx, tool_name, args, error):
        """统一的重试决策"""
        msg = str(error).lower()
        if 'timeout' in msg or 'rate limit' in msg or 'not connected' in msg:
            return 'retry'
        return 'error'

    async def after_model_request(self, ctx, messages, response, error):
        """每次 LLM 调用后检查 token"""
        if response and hasattr(response, 'usage'):
            tokens = response.usage.total_tokens
            logger.debug("LLM call: %d tokens", tokens)
            if tokens > 50000:
                await self._alert("大 Token 消耗", f"单次调用 {tokens} tokens")

    def get_toolset(self):
        if self._skill_capability:
            return self._skill_capability.get_toolset()
        return None

    async def _alert(self, title: str, content: str):
        """发送飞书告警"""
        if self.feishu_webhook:
            try:
                import httpx
                async with httpx.AsyncClient() as c:
                    await c.post(self.feishu_webhook, json={
                        "msg_type": "text",
                        "content": {"text": f"[{title}]\n{content}\n{datetime.now()}"}
                    })
            except Exception:
                logger.exception("飞书告警发送失败")

    @staticmethod
    def _calculate_cost(usage) -> float:
        return 0.0  # 简化示例
```

### 5.4 Agent 创建（一步到位）

```python
agent = Agent(
    model=model,
    capabilities=[
        AppCapability(
            feishu_webhook="https://open.feishu.cn/...",
            daily_budget=50.0,
        ),
        SkillsCapability(directories=['./skills'], auto_reload=True),
    ],
)
```

### 5.5 常见坑

**坑 1：`get_instructions` 返回重复指令**

`AbstractCapability` 的 `get_instructions()` 和 `get_toolset()` 是独立调用的。如果 toolset 内部已经通过 `get_instructions()` 提供了指令，capability 就不需要再返回——返回 `None` 即可，否则指令会重复出现两次。

**坑 2：多个 Capability 的执行顺序**

钩子按 `capabilities` 列表顺序执行。告警/监控相关的 capability 应该放在第一位（最先执行 `before_run`，最后执行 `after_run`）。

## 6. 生产环境项目结构

### 6.1 完整的生产级 Agent 创建

```python
"""
生产环境 Agent 创建 — 整合所有最佳实践。
"""

from datetime import datetime
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset, StreamableHttpTransport


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

    # 2. 创建 AppCapability（横切关注点）
    app_capability = AppCapability(
        feishu_webhook=feishu_webhook,
        daily_budget=50.0,
    )
    app_capability._mcp_toolsets = mcp_toolsets

    # 3. 创建 SkillsCapability（Agent Skills）
    skills_capability = SkillsCapability(
        directories=skill_dirs,
        auto_reload=True,
    )
    app_capability._skill_capability = skills_capability

    # 4. 创建 Agent
    agent = Agent(
        model=model,
        capabilities=[app_capability, skills_capability],
        toolsets=mcp_toolsets,
        instructions="You are a helpful assistant. Reply in Chinese.",
        retries=2,
        tool_timeout=30.0,
    )

    # 5. 注册静态工具（不需要动态发现的）
    @agent.tool_plain(retries=2)
    async def current_time() -> str:
        """获取当前服务器时间"""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return agent
```

### 6.2 启动和关闭

```python
import asyncio

_agent: Agent | None = None


async def startup():
    """应用启动：初始化 MCP 连接、扫描技能、创建 Agent"""
    global _agent

    _agent = create_production_agent(
        model=model,
        mcp_servers=[
            {"name": "web-search", "url": "...", "api_key": "..."},
            {"name": "maps", "url": "...", "api_key": "..."},
        ],
        skill_dirs=['./skills'],
        feishu_webhook="https://open.feishu.cn/...",
    )

    app_capability = _agent.capabilities[0]
    await asyncio.gather(*[
        t.__aenter__() for t in app_capability._mcp_toolsets
    ])
    logger.info("Agent 启动完成")


async def shutdown():
    """应用关闭：断开 MCP 连接"""
    app_capability = _agent.capabilities[0]
    await asyncio.gather(*[
        t.__aexit__(None, None, None) for t in app_capability._mcp_toolsets
    ], return_exceptions=True)
    logger.info("Agent 已关闭")


def get_agent() -> Agent:
    """获取 Agent 单例"""
    if _agent is None:
        raise RuntimeError("Agent 尚未初始化")
    return _agent
```

### 6.3 全貌架构图

```
                    Application
                         │
                    ┌────▼────┐
                    │  Agent  │
                    └────┬────┘
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
   capabilities[]    toolsets[]     tools[]
          │              │              │
   ┌──────▼──────┐ ┌────▼─────┐ ┌─────▼─────┐
   │AppCapability│ │MCPToolset│ │@agent.tool│
   │ (横切关注点) │ │(远程工具) │ │(静态工具)  │
   │             │ └──────────┘ └───────────┘
   │ ┌─────────┐ │
   │ │Skills   │ │
   │ │Capability│ │
   │ └────┬────┘ │
   └──────┼──────┘
          │
   ┌──────▼──────┐
   │SkillsToolset│
   │ × 4 tools   │
   └──────┬──────┘
          │
   ┌──────▼──────┐
   │ SkillRegistry│
   │ + auto-reload│
   └──────┬──────┘
          │
   ┌──────▼──────┐
   │ discovery.py│
   │ + parsing   │
   └─────────────┘
```

## 7. 速查表与对比

### 7.1 pydantic-ai 2.0 速查表

| 特性 | API | 适用场景 |
|------|-----|---------|
| 静态工具 | `@agent.tool` / `@agent.tool_plain` | 编译时已知的工具函数 |
| 动态工具集 | `FunctionToolset` + `@self.tool` | 运行时从文件/DB 加载的工具 |
| MCP 远程工具 | `MCPToolset(transport)` | 外部 MCP 服务器上的工具 |
| Agent Skills | `SkillsCapability(directories=...)` | 渐进式领域知识加载 |
| 生命周期钩子 | `AbstractCapability` | 告警/日志/cost 等横切关注点 |
| 流式输出 | `agent.run_stream()` | SSE / 实时打字效果 |
| 多轮对话 | `message_history=[...]` | Web 聊天应用 |
| 自动续跑 | `UsageLimits` + try/except | 超长对话/任务 |
| 错误恢复 | `ModelRetry` | 工具参数无效 → LLM 自动修正 |
| 重试控制 | `retries=N` / `MCPToolset(max_retries=N)` | 不稳定工具/服务 |
| 超时控制 | `tool_timeout=N` / `@self.tool(timeout=N)` | 防止工具卡死 |
| 人工审批 | `@self.tool(requires_approval=True)` | 敏感操作 |
| 参数预处理 | `BeforeValidator(_coerce_to_dict)` | LLM 传字符串 → 转 dict |

### 7.2 Tool / Toolset / MCP / Skills / Capability 对比表

| 维度 | @agent.tool | @agent.tool_plain | FunctionToolset | MCPToolset | Agent Skills | Capability |
|------|-------------|-------------------|----------------|------------|-------------|------------|
| 注册方式 | 装饰器 | 装饰器 | 子类 + @self.tool | 配置 | SKILL.md + 扫描 | 继承 AbstractCapability |
| 动态发现 | 否 | 否 | 是 | 是（远程） | 是（文件） | 否 |
| RunContext | 需要 | 不需要 | 需要 | 自动 | 自动 | N/A |
| 重试 | retries=N | retries=N | retries=N | tool_error_behavior | ModelRetry | on_tool_execute_error |
| 生命周期钩子 | 无 | 无 | 无 | process_tool_call | 无 | 30 个钩子 |
| 适用阶段 | 入门 | 入门 | 工程篇 | 工程篇 | 工程篇 | 工程篇 |

## 8. 安全与测试清单

### 安全检查项

- [ ] **上下文窗口管理**：是否设置了 `UsageLimits`？（防止无限 token 消耗）
- [ ] **工具权限**：是否需要 `requires_approval=True`？（敏感操作如数据库写入、发送邮件）
- [ ] **MCP 连接安全**：MCP transport 是否使用了 HTTPS + API key？
- [ ] **技能路径穿越**：`_discover_resources` 中的 symlink 检测是否启用？
- [ ] **成本预算**：是否设置了 `daily_budget` 或 `total_tokens_limit`？
- [ ] **错误暴露**：tool 函数是否捕获并 sanitize 了原始错误信息？（避免 LLM 输出内部路径）
- [ ] **重试风暴**：retries 和 max_continues 是否设置了合理的上限？

### 测试类型清单

| 测试类型 | 覆盖内容 | 示例 |
|---------|---------|------|
| 单元测试 | 单个 tool 函数 | `_safe_eval("2 + 3") == 5` |
| 集成测试 | Agent + tools | `agent.run("天气")` 确认 tool 被调用 |
| 重试测试 | retries / ModelRetry | mock 工具抛异常，确认自动重试 |
| 安全测试 | 路径穿越、prompt injection | `../../etc/passwd` 作为参数 |
| 成本测试 | UsageLimits 行为 | 确认超限时正确抛出异常 |
| 多轮对话测试 | message_history | 第 2 轮输入 "它呢"，确认引用正确 |
| 并发测试 | 多个 Agent run | 确认 MCP 连接不被并发竞争破坏 |

## 本章练习

1. 用 MCPToolset 接入一个真实 MCP 服务器（如 GitHub MCP、文件系统 MCP）
2. 创建一个包含 SKILL.md 的自定义技能（包含 YAML 前页 + 指令 + 模板资源）
3. 为 Agent 添加日志和告警 capability（继承 AbstractCapability 实现）

## 验收标准

你能设计并实现一个集成 MCP + Skills + 日志告警的生产级 Agent。
