# Pydantic AI 从入门到精通

> 基于 pydantic-ai 2.0，独立最小示例，最佳实践。

---

## 目录

1. [Hello World：第一个流式 Agent](#1-hello-world第一个流式-agent)
2. [Tool 注册：三种方式与选择策略](#2-tool-注册三种方式与选择策略)
3. [MCP Tool：远程工具集成](#3-mcp-tool远程工具集成)
4. [Loop Agent：多轮对话与自动续跑](#4-loop-agent多轮对话与自动续跑)
5. [Agent Skills：渐进式能力加载](#5-agent-skills渐进式能力加载)
6. [Capability 生命周期：AOP 钩子与统一横切](#6-capability-生命周期aop-钩子与统一横切)
7. [生产环境最佳实践](#7-生产环境最佳实践)

---

## 1. Hello World：第一个流式 Agent

### 1.1 最小可运行代码

```python
import asyncio
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from openai import AsyncOpenAI

# 1. 创建模型
client = AsyncOpenAI(
    api_key="your-api-key",
    base_url="https://api.deepseek.com/v1",
)
model = OpenAIChatModel(
    "deepseek-chat",
    provider=OpenAIProvider(openai_client=client),
)

# 2. 创建 Agent
agent = Agent(
    model=model,
    instructions="You are a helpful assistant. Reply in Chinese.",
)

# 3. 流式调用
async def main():
    async with agent.run_stream("介绍一下 Python 的 asyncio") as result:
        async for event in result.stream():
            if hasattr(event, 'delta') and event.delta:
                print(event.delta, end='', flush=True)

asyncio.run(main())
```

### 1.2 发生了什么

```
用户消息 "介绍一下 Python 的 asyncio"
  │
  ▼
Agent.run_stream()
  ├── 组装 messages:
  │     [SystemPrompt(instructions), UserPrompt("介绍一下 Python 的 asyncio")]
  │
  ├── 调用 LLM（流式）
  │     POST /v1/chat/completions  {stream: true}
  │
  └── 返回 AsyncIterator[AgentStreamEvent]
        ├── PartStartEvent(delta="Python")
        ├── PartDeltaEvent(delta=" 的")
        ├── PartDeltaEvent(delta=" asyncio")
        ├── ...
        └── PartEndEvent + RunFinalizedEvent
```

**关键点**：`run_stream()` 返回的 `result` 既是异步迭代器（`result.stream()`），也持有最终结果（`result.data`）。流式过程中不要访问 `result.data`——它只在流结束后才有值。

### 1.3 常见坑

**坑 1：在流结束前访问 `result.data`**

```python
# ❌ 错误
async with agent.run_stream("hello") as result:
    print(result.data)  # None！流还没结束
    async for event in result.stream():
        ...

# ✅ 正确
async with agent.run_stream("hello") as result:
    async for event in result.stream():
        ...
    print(result.data)  # 最终结果在这里
```

**坑 2：忘记 `flush=True`**

```python
# ❌ 终端可能缓冲，看不到实时输出
print(event.delta, end='')

# ✅ 强制刷新
print(event.delta, end='', flush=True)
```

**坑 3：用错模型名**

`OpenAIChatModel` 的第一个参数是**模型提供商的模型名**（如 `"deepseek-chat"`、`"gpt-4o"`），不是你自定义的代号。Provider 的 `base_url` 决定了请求发往哪里。

---

## 2. Tool 注册：三种方式与选择策略

### 2.1 方式一：`agent.tool` 装饰器（推荐用于静态工具）

```python
from pydantic_ai import Agent, RunContext

agent = Agent(model=model, instructions="You are a helpful assistant.")

@agent.tool
async def get_weather(ctx: RunContext, city: str) -> str:
    """获取指定城市的天气信息。

    Args:
        city: 城市名，例如 "北京" 或 "Shanghai"。
    """
    # 实际项目中替换为 API 调用
    weather_data = {"北京": "晴 25°C", "上海": "多云 28°C"}
    return weather_data.get(city, f"未找到 {city} 的天气数据")

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

**关键原理**：
- `@agent.tool` 从函数签名自动生成参数 schema（`city: str` → LLM 看到"需要一个 string 类型的 city 参数"）
- docstring 的 `Args:` 部分会自动映射为参数描述——LLM 会看到每个参数的含义
- `ctx: RunContext` 永远是第一个参数（即使不用），框架通过它注入 Agent 状态

### 2.2 方式二：`agent.tool_plain`（不需要 ctx 时更简洁）

```python
from datetime import datetime

@agent.tool_plain
async def current_time() -> str:
    """获取当前服务器时间。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

@agent.tool_plain(retries=2)
async def get_user_count() -> int:
    """获取当前在线用户数。"""
    return 42
```

**与 `@agent.tool` 的区别**：不需要 `ctx: RunContext` 参数。适用于纯工具函数，不依赖 Agent 状态。

### 2.3 方式三：`FunctionToolset`（动态发现的工具）

当工具列表不是编译时确定的（比如从文件系统、数据库加载），用 `FunctionToolset` 子类：

```python
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai import RunContext

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
toolset = DatabaseToolset([
    {"name": "search_users", "description": "搜索用户"},
    {"name": "create_order", "description": "创建订单"},
])
agent = Agent(model=model, toolsets=[toolset])
```

**关键原理**：`@self.tool` 自动处理 schema 生成、参数验证、工具注册——不需要手写 `ToolDefinition`。`FunctionToolset.get_tools()` 是框架自动调用的，你写工具函数就行。

### 2.4 选择策略

```
工具是在写代码时就知道的？
  ├── 是 → @agent.tool / @agent.tool_plain
  └── 否 → FunctionToolset 子类
       ├── 工具来自外部系统（文件、数据库、MCP）？ → FunctionToolset
       └── 工具来自网络上的 MCP 服务器？ → MCPToolset（见第 3 章）
```

### 2.5 常见坑

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

**坑 2：docstring 描述不够清晰**

LLM 靠工具描述决定是否调用。描述要包含**什么时候用**和**参数含义**：

```python
# ❌ 描述太模糊
@agent.tool
async def search(ctx: RunContext, q: str) -> str:
    """搜索"""
    ...

# ✅ 清晰的描述
@agent.tool
async def search_users(ctx: RunContext, name: str) -> str:
    """根据姓名搜索用户。当需要查找特定用户信息时使用。

    Args:
        name: 用户的姓名或姓名的一部分，支持模糊匹配。
    """
    ...
```

**坑 3：在 tool 函数内做重试逻辑**

```python
# ❌ 不要自己实现重试
@agent.tool
async def unstable_api(ctx: RunContext) -> str:
    for i in range(3):  # 手写重试
        try:
            return await call_api()
        except Exception:
            await asyncio.sleep(1)

# ✅ 用框架内置的 retries
@agent.tool(retries=3)
async def unstable_api(ctx: RunContext) -> str:
    return await call_api()
```

框架的 retries 机制会在失败时自动触发 ModelRetry，LLM 能感知到重试并调整参数。

---

## 3. MCP Tool：远程工具集成

### 3.1 最小可运行代码

MCP（Model Context Protocol）允许 Agent 调用远程服务器上的工具，就像调用本地工具一样。

```python
from pydantic_ai.mcp import MCPToolset, StreamableHttpTransport

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
agent = Agent(
    model=model,
    toolsets=[mcp_toolset],
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

---

## 4. Loop Agent：多轮对话与自动续跑

### 4.1 核心概念

pydantic-ai 的 Agent 是无状态的——每次 `run_stream()` 是独立的调用。要实现多轮对话，需要自己管理消息历史：

```
User: "北京三日游"
  → Agent.run_stream(user_msg, message_history=[])
  → [Agent 回复 + Tool 调用] 存入 message_history

User: "加点亲子景点"
  → Agent.run_stream(user_msg, message_history=[之前的全部消息])
  → 继续存入...

User: "导出 PDF"
  → Agent.run_stream(user_msg, message_history=[...])
```

### 4.2 最小 Loop Agent 实现

```python
import asyncio
from dataclasses import dataclass, field
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse


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
            message_history=self.messages,  # ← 关键：传入历史
        ) as result:
            async for event in result.stream():
                if hasattr(event, 'delta') and event.delta:
                    print(event.delta, end='', flush=True)
                    full_reply += event.delta

        # 将本轮对话追加到历史
        self.messages.append(ModelRequest(parts=[user_input]))
        self.messages.append(ModelResponse(parts=[full_reply]))
        print()  # 换行
        return full_reply


async def main():
    conv = Conversation(agent=agent)

    while True:
        try:
            user_input = input("\n你: ").strip()
            if user_input.lower() in ("exit", "quit", "q"):
                break
            print("AI: ", end="")
            await conv.chat(user_input)
        except KeyboardInterrupt:
            break


asyncio.run(main())
```

### 4.3 自动续跑（Auto-Continue）

当对话 token 超过 LLM 的上下文限制时，手动管理 `ModelMessage` 会被框架触发 `UsageLimitExceeded`。自动续跑解决这个问题：

```python
from pydantic_ai.usage import UsageLimits

async def chat_with_continue(conv: Conversation, user_input: str):
    """带自动续跑的对话"""
    max_continues = 2  # 最多续跑 2 次
    continue_count = 0

    while True:
        try:
            full_reply = ""
            async with conv.agent.run_stream(
                user_input if continue_count == 0 else "继续",
                message_history=conv.messages,
                usage_limits=UsageLimits(
                    request_limit=50,           # 最多 50 个请求
                    total_tokens_limit=100_000,  # 总 token 上限
                ),
            ) as result:
                async for event in result.stream():
                    if hasattr(event, 'delta') and event.delta:
                        print(event.delta, end='', flush=True)
                        full_reply += event.delta

            # 正常完成，追加到历史
            conv.messages.append(ModelRequest(parts=[user_input]))
            conv.messages.append(ModelResponse(parts=[full_reply]))
            print()
            return full_reply

        except Exception as e:
            if "UsageLimitExceeded" in str(type(e).__name__):
                continue_count += 1
                if continue_count > max_continues:
                    print("\n[已达到最大续跑次数]")
                    raise
                print(f"\n[续跑 {continue_count}/{max_continues}...]")
                # 注意：框架已经把部分响应写入了 message_history
                # 不需要手动拼接，直接让 Agent 继续
                user_input = "继续执行未完成的任务"
            else:
                raise
```

### 4.4 常见坑

**坑 1：忘记传 `message_history`**

```python
# ❌ 每次调用都是"新对话"
await agent.run_stream("天气怎么样")
await agent.run_stream("那明天呢")  # Agent 不知道"那"指什么

# ✅ 传入历史
messages = []
result1 = await agent.run_stream("天气怎么样", message_history=messages)
# 把 result1 的 message 加入到 messages
result2 = await agent.run_stream("那明天呢", message_history=messages)
```

**坑 2：手拼 message 格式**

```python
# ❌ 不要自己构建 ModelMessage 的底层数据结构
messages.append({"role": "user", "content": "hello"})  # 格式可能和框架不一致

# ✅ 用 run_stream 返回的 result.new_messages()
async with agent.run_stream(user_input, message_history=messages) as result:
    ...
messages.extend(result.new_messages())  # 框架保证格式正确
```

**坑 3：不设 `UsageLimits`**

没有 token 限制时，长对话可能耗尽 API 额度。`UsageLimits` 是安全阀——不是性能优化，是成本控制。

---

## 5. Agent Skills：渐进式能力加载

### 5.1 为什么需要 Agent Skills

普通的 tool 注册方式有一个问题：你全部注册，LLM 全部看到。当领域指令很长（几百行的攻略模板、多步骤工作流），每一次对话都会被塞进 system prompt，烧 token 且可能干扰 LLM。

Agent Skills 规范的解决思路是**渐进式披露**：

```
L0: 启动时扫描文件系统 → 只记住名称和一句话描述
L1: Agent 调用 list_skills → 看到可用技能目录（~50 tokens）
L2: Agent 调用 load_skill(name) → 拿到完整指令（~1000 tokens）
L3: Agent 调用 read_skill_resource / run_skill_script → 按需用具体资源
```

### 5.2 Skill 目录结构

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

### 5.3 最佳实践：SkillsToolset + SkillsCapability

```python
"""
完整的 Agent Skills 实现。
参考 pydantic-ai-skills 的架构，约 200 行代码实现
4 个标准工具（list/load/read/run）+ SkillsCapability 一行式集成。
"""

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Annotated

import yaml
from pydantic import BeforeValidator
from pydantic_ai import Agent, ModelRetry, RunContext
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
_SCRIPT_EXTENSIONS  = {'.py', '.sh', '.js', '.ts'}   # 可执行脚本扩展名


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
        path = skill_dir.resolve()

        skills[name] = SkillMeta(
            name=name,
            path=path,
            description=str(frontmatter.get('description', '')),
            body=body.strip(),
            resources=_discover_resources(path),
            scripts=_discover_scripts(path),
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


# ═══════════════════════════════════════════════════════
# Layer 4: 注册表
# ═══════════════════════════════════════════════════════

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
                return r.content  # lazy load 在这里触发
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
# Layer 5: Toolset（FunctionToolset + 4 标准工具）
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
        self._registry = SkillRegistry(
            base_dirs=[Path(d) for d in self.directories]
        )
        self._registry.scan()  # 一次性扫描全部目录并合并
        self._toolset = SkillsToolset(self._registry, auto_reload=self.auto_reload)

    def get_toolset(self) -> SkillsToolset:
        return self._toolset

    def get_instructions(self):
        return None  # toolset.get_instructions() 按需提供


# ═══════════════════════════════════════════════════════
# 使用示例
# ═══════════════════════════════════════════════════════

async def main():
    agent = Agent(
        model=model,
        capabilities=[SkillsCapability(directories=['./skills'], auto_reload=True)],
    )

    async with agent.run_stream("帮我规划一个北京三日游") as result:
        async for event in result.stream():
            if hasattr(event, 'delta') and event.delta:
                print(event.delta, end='', flush=True)


if __name__ == '__main__':
    asyncio.run(main())
```

### 5.4 关键原理

**为什么用 `ModelRetry` 而不是返回错误字符串**：

```python
# ❌ 返回错误字符串 — LLM 行为不确定
if not found:
    return "技能不存在"

# ✅ ModelRetry — 框架让 LLM 自动重试
if not found:
    raise ModelRetry(
        f"技能 '{name}' 不存在。可用: {available}。请调用 list_skills 后重试。"
    )
```

ModelRetry 把错误信息注入对话历史，LLM 在下一轮迭代中读到并自动修正调用参数——用户无感。

**为什么 `auto_load_references` 不违反渐进式加载**：

某些资源是指令的语义组成部分（如输出模板）。如果不自动加载，Agent 要先读指令、发现需要模板、再调一次 `read_skill_resource`——纯浪费一轮 round-trip。这个优化消除了 L2→L3 的无意义来回，但没有削弱 L0→L2 的 token 节省。

### 5.5 常见坑

**坑 1：技能名不规范**

```yaml
# ❌ 不符合规范
name: My_Travel_Skill

# ✅ 符合 Agent Skills 规范
name: travel-planner
```

技能名必须是小写字母、数字、连字符，最多 64 字符，不含 `anthropic` 或 `claude`。

**坑 2：忘记处理 `load_skill` 失败**

LLM 可能拼错技能名。一定要用 `ModelRetry` 而不是 `return "error"`——后者让 LLM 不知道该怎么修正。

**坑 3：资源文件太大**

`auto_load_references` 把所有指定资源内联到 `load_skill` 返回中。如果一个资源文件几千行，考虑不要 auto-load——让 Agent 通过 `read_skill_resource` 按需读取。

---

## 6. Capability 生命周期：AOP 钩子与统一横切

### 6.1 为什么需要 Capability

当前典型的工具管理有多个横切关注点——告警、日志、重试、cost 统计——分散在不同位置：

```
make_guarded(current_time)    ← 告警包装
make_guarded(ask_human)       ← 告警包装
AlertingMCPToolset.call_tool  ← 告警 + 重连
SkillToolset                  ← 无告警、无重试  ← 缺口
```

`AbstractCapability` 提供 **30 个生命周期钩子**，把这些横切关注点统一到一个入口。

### 6.2 所有钩子一览

```python
from pydantic_ai.capabilities import AbstractCapability

class AppCapability(AbstractCapability):
    """聚合所有横切关注点"""

    # ── 工具注册 ──
    def get_toolset(self) -> FunctionToolset | None:
        """返回工具集（与 get_native_tools 二选一）"""
        ...

    def get_native_tools(self) -> list:
        """返回原生工具函数列表（与 get_toolset 二选一）"""
        ...

    def get_instructions(self) -> AgentInstructions | None:
        """注入 system prompt 指令"""
        ...

    # ── Run 生命周期 ──
    async def before_run(self, ctx, run_context):
        """每个 Agent run 开始前（初始化资源）"""
        ...

    async def after_run(self, ctx, run_context, result, error):
        """每个 Agent run 结束后（清理、持久化 cost）"""
        ...

    # ── Tool 生命周期 ──
    async def before_tool_execute(self, ctx, tool_name, args):
        """工具执行前（校验参数、记录审计日志）"""
        ...

    async def after_tool_execute(self, ctx, tool_name, args, result, error):
        """工具执行后（告警、更新 metrics）"""
        if error:
            await send_alert(f"Tool '{tool_name}' 异常: {error}")

    async def on_tool_execute_error(self, ctx, tool_name, args, error):
        """决定重试还是放弃"""
        if 'timeout' in str(error).lower():
            return 'retry'
        return 'error'

    # ── Model 生命周期 ──
    async def before_model_request(self, ctx, messages):
        """模型请求前（修改 messages、注入 few-shot 示例）"""
        ...

    async def after_model_request(self, ctx, messages, response, error):
        """模型请求后（检查 token 消耗）"""
        ...

    # ── 更多钩子 ──
    # before_node_run / after_node_run
    # before_tool_validate / after_tool_validate
    # before_output_process / after_output_process
    # on_model_request_error / on_node_run_error
    # wrap_model_request / wrap_tool_execute / wrap_run_event_stream
```

### 6.3 核心用法：统一告警 + Cost 统计

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

    # ── 统一每个 run 的入口和出口 ──

    async def before_run(self, ctx, run_context):
        """Run 开始：初始化资源"""
        logger.info("Agent run 开始")
        # 确保 MCP 连接可用
        for toolset in self._mcp_toolsets:
            if not toolset.is_running:
                await toolset.__aenter__()

    async def after_run(self, ctx, run_context, result, error):
        """Run 结束：持久化统计数据"""
        if error:
            logger.error("Agent run 失败: %s", error)
            await self._alert("Agent Run 异常", str(error))
        # 持久化 cost
        if hasattr(run_context, 'usage'):
            cost = self._calculate_cost(run_context.usage)
            self._total_cost += cost
            logger.info("Run cost: $%.4f (累计: $%.2f)", cost, self._total_cost)
            if self._total_cost > self.daily_budget:
                await self._alert("Cost 超预算", f"累计 ${self._total_cost:.2f}")

    # ── 统一所有工具的告警 ──

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
        if 'timeout' in msg:
            return 'retry'
        if 'rate limit' in msg:
            return 'retry'
        if 'not connected' in msg:
            return 'retry'
        return 'error'

    # ── 统一模型请求监控 ──

    async def after_model_request(self, ctx, messages, response, error):
        """每次 LLM 调用后检查 token"""
        if response and hasattr(response, 'usage'):
            tokens = response.usage.total_tokens
            logger.debug("LLM call: %d tokens", tokens)
            if tokens > 50000:
                await self._alert("大 Token 消耗", f"单次调用 {tokens} tokens")

    # ── 工具注册 ──

    def get_toolset(self):
        """返回 Skill toolset"""
        if self._skill_capability:
            return self._skill_capability.get_toolset()
        return None

    # ── 内部辅助 ──

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
        # 按模型定价计算实际成本
        return 0.0  # 简化示例
```

### 6.4 Agent 创建（一步到位）

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
    # MCP toolsets 也通过 capabilities 管理
    # 不再需要单独的 toolsets= 和 tool_plain=
)
```

### 6.5 常见坑

**坑 1：`get_instructions` 返回重复指令**

`AbstractCapability` 的 `get_instructions()` 和 `get_toolset()` 是独立调用的。如果 toolset 内部已经通过 `get_instructions()` 提供了指令，capability 就不需要再返回——返回 `None` 即可，否则指令会重复出现两次。

**坑 2：多个 Capability 的执行顺序**

钩子按 `capabilities` 列表顺序执行。告警/监控相关的 capability 应该放在第一位（最先执行 `before_run`，最后执行 `after_run`）。

---

## 7. 生产环境最佳实践

### 7.1 完整的生产级 Agent 创建

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

### 7.2 启动和关闭

```python
import asyncio

_agent: Agent | None = None


async def startup():
    """应用启动：初始化 MCP 连接、扫描技能、创建 Agent"""
    global _agent

    # 创建 Agent
    _agent = create_production_agent(
        model=model,
        mcp_servers=[
            {"name": "web-search", "url": "...", "api_key": "..."},
            {"name": "maps", "url": "...", "api_key": "..."},
        ],
        skill_dirs=['./skills'],
        feishu_webhook="https://open.feishu.cn/...",
    )

    # 初始化 MCP 连接（并发）
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

### 7.3 pydantic-ai 2.0 速查表

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

### 7.4 回顾：全貌

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

---

**本文完。**
