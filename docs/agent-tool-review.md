# pydantic-ai-skills 对比审查：Agent Tool 层实现

> 日期: 2026-07-02 | 审查范围: `src/agent/` 下的 tool 注册、执行、错误处理

---

## 审查结论摘要

fastapi-agent-pydanticai 的 Agent Tool 层存在**三个架构不一致**和一个**设计降级**：

| 问题 | 严重度 | 影响 |
|------|--------|------|
| 三种工具注册方式各自为政 | 中 | 错误处理、告警、重试逻辑不统一 |
| SkillToolset 手写 AbstractToolset | 中 | 80 行胶水代码，不如 `FunctionToolset` + `@self.tool` |
| Skill 工具无 ModelRetry | 高 | LLM 拼错技能名 → 返回错误字符串 → 不确定的恢复行为 |
| make_guarded 不覆盖 skill/MCP 工具 | 低 | alerting 缺口 |

---

## 1. 当前工具注册全景

fastapi-agent-pydanticai 的 Agent 使用**三种不同机制**注册工具：

```
model_client.py:init_agent()

  ├── [方式 1] MCP 工具: toolsets=[AlertingMCPToolset × 3]
  │     ├── 错误处理: call_tool() 覆盖 → 断线重连 + 飞书告警
  │     ├── 注册: MCPToolset(MCP 协议自动发现)
  │     └── ~50 行自定义代码

  ├── [方式 2] Skill 工具: toolsets=[SkillToolset(registry)]
  │     ├── 错误处理: ❌ 无（try/except pass 吞错误）
  │     ├── 注册: 手写 get_tools()/call_tool() (AbstractToolset)
  │     └── ~80 行胶水代码

  ├── [方式 3] 普通工具: agent.tool_plain() × 2
  │     ├── 错误处理: make_guarded() → 日志 + 飞书告警 + 重新抛出
  │     ├── 注册: pydantic-ai 内置 tool_plain()
  │     └── ~15 行包装代码

  └── 错误处理覆盖矩阵:
        MCP tools:   ✅ 飞书告警 + 重连
        普通 tools:  ✅ 飞书告警 + 日志
        Skill tools: ❌ 无保护
```

### 1.1 MCP 工具层（`mcp.py`）

```python
class AlertingMCPToolset(MCPToolset):
    async def call_tool(self, name, tool_args, ctx, tool):
        try:
            return await super().call_tool(name, tool_args, ctx, tool)
        except Exception as e:
            if 'not connected' in str(e).lower():
                await self._reconnect()     # 断线重连
                return await super().call_tool(...)  # 重试一次
            # 所有异常 → 飞书告警
            asyncio.create_task(_alert(...))
            raise
```

**优点**：连接弹性 + 可观测性兼备。  
**隐患**：重连是 per-call 触发的，如果 MCP server 确实挂了，每次 tool call 都会尝试重连一次才失败——增加 latency。

### 1.2 Skill 工具层（`toolset.py`）

```python
class SkillToolset(AbstractToolset):
    # 手写 3 个方法:
    async def get_instructions(self, ctx): ...    # 注入 L1 技能列表
    async def get_tools(self, ctx):               # 为每个 skill 创建 ToolsetTool
        tools[tool_name] = ToolsetTool(
            toolset=self,
            tool_def=ToolDefinition(name=..., description=...),  # 手动构建
            max_retries=1,
            args_validator=_empty_validator(),    # 空 dict 校验
        )
    async def call_tool(self, name, tool_args, ctx, tool):  # 手动 dispatch
        return await self._loaders[self._tool_to_skill[name]]()
```

**问题**：
- 80 行胶水代码，等价于 `FunctionToolset.__init__` 的自动行为
- `_empty_validator()` 接受任何 dict → 拼写错误静默传递
- `_tool_to_skill` 字典手动维护 → 多一个"注册但忘记映射"的 bug 来源

### 1.3 普通工具层（`tool_guard.py`）

```python
def make_guarded(name: str, func):
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception:
            logger.exception(...)
            asyncio.create_task(send_feishu_alert(...))
            raise
    wrapper.__name__ = name
    return wrapper
```

**优点**：简洁的装饰器模式。  
**缺憾**：只覆盖了 `tool_plain` 注册的 2 个工具，对 skill 工具和 MCP 工具无效。

---

## 2. 与 pydantic-ai-skills 的逐项对比

### 2.1 工具注册：`AbstractToolset` 手写 vs `FunctionToolset` + `@self.tool`

| | fastapi v1 | pydantic-ai-skills | 差距 |
|---|---|---|---|
| 注册方式 | 手写 `get_tools()` 构建 `ToolsetTool` dict | `@self.tool` 装饰器自动生成 | `@self.tool` 少 40 行 |
| 参数 schema | `_empty_validator()`（空 dict） | 自动从函数签名 + docstring 生成 | LLM 看不到参数说明 |
| 工具描述 | 手动拼字符串 | 自动从 docstring 提取 | 描述与代码不一致风险 |
| dispatch | `_tool_to_skill` dict 手动映射 | `FunctionToolset.call_tool()` 内置 | 多一个 bug 来源 |

**建议**：`SkillToolset` 应继承 `FunctionToolset` 而非 `AbstractToolset`。

```python
# 当前 ~80 行 → 改为 ~60 行 + 更干净
class SkillToolset(FunctionToolset):
    def __init__(self, registry, ...):
        super().__init__()
        self._registry = registry
        self._register_tools()

    @self.tool
    async def list_skills(_ctx: RunContext) -> dict[str, str]:
        """获取所有可用技能的名称和描述..."""
        ...

    @self.tool
    async def load_skill(ctx: RunContext, skill_name: str) -> str:
        """加载指定技能的完整指令。skill_name 必须与 list_skills 输出一致..."""
        ...
```

### 2.2 错误处理：返回字符串 vs ModelRetry

这是**最重要的差异**。

**fastapi 当前做法**：

```python
# toolset.py: _make_loader()
async def load() -> str:
    try:
        body = registry.get_skill_prompt(name)
    except Exception:
        return f"无法加载能力: {name}"   # ← 返回错误字符串
    ...
    for ref_name in skill.auto_load_references:
        try:
            refs.append(registry.get_skill_reference(name, ref_name))
        except Exception:
            pass  # ← 静默忽略
```

**pydantic-ai-skills 的做法**：

```python
@self.tool
async def load_skill(ctx: RunContext, skill_name: str) -> str:
    if skill_name not in self._skills:
        available = ', '.join(sorted(self._skills.keys())) or 'none'
        raise ModelRetry(
            f"Skill '{skill_name}' not found. Available skills: {available}. "
            'Call list_skills to see options and try again with an exact name.'
        )
```

**差异**：

| | 返回错误字符串 | ModelRetry |
|---|---|---|
| LLM 行为 | 不确定（可能忽略、幻觉、重复尝试） | 框架自动重试，LLM 收到明确修正指令 |
| Agent 流 | 继续（可能带着错误上下文继续） | 框架消耗一次 retry，修正后继续 |
| 用户体验 | 用户看到错误信息 | 用户无感，LLM 自动修正 |
| 调试 | 日志里查不到 | 框架记录重试次数，可观测 |

**建议**：所有 skill 工具的 `KeyError`/`ValueError` 路径都应改用 `ModelRetry`。

### 2.3 参数处理：`_empty_validator` vs `BeforeValidator`

**fastapi**：

```python
args_validator=_empty_validator()  # 接受任何 dict 或 None
```

**pydantic-ai-skills**：

```python
args: Annotated[dict[str, Any] | None, BeforeValidator(_coerce_to_dict)] = None
```

`BeforeValidator(_coerce_to_dict)` 自动将 LLM 可能传来的 JSON 字符串（`'{"max-papers": 5}'`）转成 dict。fastapi 当前接收不到这种参数——如果 LLM 传字符串会直接验证失败。

### 2.4 初始化：手动三步 vs 原子构造

**fastapi**：

```python
# init_app.py — 3 步，步骤 2 可能被忘记
registry = SkillRegistry()
registry.scan_skills()             # ← 忘了？toolset 是空的，不报错
init_agent(registry)
```

**pydantic-ai-skills**：

```python
# 一步原子化，构造完成 = 就绪
agent = Agent(capabilities=[SkillsCapability(directories=['./skills'])])
```

`SkillsCapability.__post_init__` 保证 scan 一定在 toolset 创建之前完成——消除中间状态。

### 2.5 工具排除机制

pydantic-ai-skills 有 `exclude_tools` 参数允许编译时禁用特定工具（如 `run_skill_script`）：

```python
def __init__(self, exclude_tools=None):
    ...
    if 'run_skill_script' not in self._exclude_tools:
        self._register_run_skill_script()
```

fastapi 当前无此能力——所有 skill 工具无条件暴露。

### 2.6 死代码

```python
# registry.py:48-59 — 从未被调用
def as_pydantic_ai_tools(self) -> list:
    """将每个 skill 作为一个 function tool 注入 agent。"""
    ...
```

这个方法存在于 `SkillRegistry` 中但从未被任何调用方使用——Skill 工具通过 `SkillToolset` 注册，不经过这个路径。

---

## 3. 发现的具体问题清单

### 3.1 高优先级

| # | 问题 | 文件 | 行号 | 建议 |
|---|------|------|------|------|
| 1 | Skill 工具无 ModelRetry，拼错名返回错误字符串 | `skills/toolset.py` | 21-36 | 参考 pydantic-ai-skills toolset.py:547-552 |
| 2 | try/except: pass 吞 L3 加载错误 | `skills/toolset.py` | 32-33 | 改为 catch + logging.warning |
| 3 | SkillToolset 手写 AbstractToolset，应该用 FunctionToolset | `skills/toolset.py` | 40-81 | 改为 `class SkillToolset(FunctionToolset)` |

### 3.2 中优先级

| # | 问题 | 文件 | 行号 | 建议 |
|---|------|------|------|------|
| 4 | 初始化分三步，易忘 scan | `init_app.py` | 103-108 | 改为 SkillsCapability 一行式 |
| 5 | 无 auto-reload | — | — | 加 `auto_reload=True` 参数 |
| 6 | `_empty_validator` 不处理 JSON 字符串 | `skills/toolset.py` | 10-11, 76 | 改为 BeforeValidator(_json_to_dict) |
| 7 | 无工具排除开关 | — | — | 加 `exclude_tools` 参数 |
| 8 | AlertingMCPToolset 重连是 per-call 的 | `mcp.py` | — | 加指数退避或最大重连次数 |

### 3.3 低优先级

| # | 问题 | 文件 | 行号 | 建议 |
|---|------|------|------|------|
| 9 | `as_pydantic_ai_tools()` 死代码 | `skills/registry.py` | 48-59 | 删除 |
| 10 | `tools/__pycache__` 残留旧模块缓存 | `tools/__pycache__/` | — | 清理 |
| 11 | make_guarded 不覆盖 skill/MCP 工具 | `tool_guard.py` | — | 在各 toolset 的 call_tool 加入统一告警 |
| 12 | `split("---", 2)` 在 body 含 `---` 时截断 | `skills/loader.py` | 63 | 改为逐行扫描 |

---

## 4. 工具错误处理统一方案

```
                ┌──────────────────────────────────┐
                │       工具调用入口                  │
                └──────────────┬───────────────────┘
                               │
               ┌───────────────┼───────────────┐
               ▼               ▼               ▼
        ┌────────────┐ ┌────────────┐ ┌──────────────┐
        │ MCP 工具    │ │ Skill 工具  │ │ 普通工具      │
        │ (Toolset)   │ │ (Toolset)   │ │ (tool_plain)  │
        └──────┬─────┘ └──────┬─────┘ └──────┬───────┘
               │              │               │
        ┌──────▼──────┐┌──────▼──────┐┌──────▼──────┐
        │ 连接丢失?    ││ 参数无效?    ││ 业务异常     │
        │ → 重连+重试  ││ → ModelRetry ││ → 日志+告警  │
        │             ││             ││   +重抛      │
        │ 其他异常?    ││ 其他异常?    ││             │
        │ → 告警+重抛 ││ → 告警+重抛  ││             │
        └─────────────┘└─────────────┘└─────────────┘
```

核心原则：**LLM 可控的用 ModelRetry（自动恢复），系统级用告警+重抛（人工介入）。**

---

## 5. 修复计划

**目标**：一次性重构到最佳实践架构，不分步演进。Feature Flag 控制新旧切换。

### 5.1 文件清单

| # | 文件 | 操作 | 角色 |
|---|------|------|------|
| 1 | `app_capability.py` | **新建** | 统一生命周期钩子：告警/重试/cost（替代 make_guarded + AlertingMCPToolset） |
| 2 | `skills/_parsing.py` | **新建** | 逐行扫描 YAML + 规范校验 |
| 3 | `skills/types.py` | **新建** | SkillMeta / SkillResource / SkillScript 数据类 |
| 4 | `skills/discovery.py` | **新建** | 文件/资源/脚本发现 + symlink 安全 |
| 5 | `skills/execution.py` | **新建** | shebang + subprocess + 超时 kill |
| 6 | `skills/toolset.py` | **重写** | AbstractToolset → FunctionToolset + @self.tool × 4 + ModelRetry |
| 7 | `skills/registry.py` | **重写** | + auto-reload 原子替换 + get_resource() |
| 8 | `skills/capability.py` | **新建** | SkillsCapability — 一行式集成 |
| 9 | `mcp.py` | **重构** | 删除 AlertingMCPToolset → MCPToolset + process_tool_call |
| 10 | `model_client.py` | **重构** | 切换到 capabilities 模式 |
| 11 | `tool_guard.py` | **删除** | 逻辑迁移到 AppCapability |
| 12 | `skills/loader.py` | **删除** | 功能拆分到 discovery + _parsing |

### 5.2 实施步骤

```
Step 1 — skills 基础设施（新建，零风险）
  _parsing.py + types.py + discovery.py + execution.py

Step 2 — skills 集成层（重写，覆盖旧文件）
  registry.py(重写) + toolset.py(重写) + capability.py(新建)
  删除 loader.py

Step 3 — Agent tool 层统一（重构，核心变更）
  app_capability.py(新建) + mcp.py(重构) + model_client.py(重构)
  删除 tool_guard.py

Step 4 — 清理
  删除 as_pydantic_ai_tools()、清理 __pycache__
```

### 5.3 迁移策略

```python
# Feature Flag 切换
def get_agent(use_v2: bool = None) -> Agent:
    if use_v2 is None:
        use_v2 = settings.SKILL_V2_ENABLED
    if use_v2:
        return _init_agent_v2()
    return _init_agent_v1()
```

上线：`Flag on → 1 周观察 → 默认 on → 删旧代码`。

### 5.4 一次性解决的全部问题

详细的每个 fix 对应的问题、怎么做、代码 diff 见 [skill-architecture-v2.md](./skill-architecture-v2.md)。此处只列清单：

| # | 问题 | 修复 |
|---|------|------|
| 1 | 三种注册方式各自为政 | `AppCapability` 统一告警/重试/cost |
| 2 | SkillToolset 手写 AbstractToolset | `FunctionToolset` + `@self.tool` |
| 3 | Skill 工具无 ModelRetry | KeyError → `ModelRetry` |
| 4 | make_guarded 只覆盖 2 个工具 | `after_tool_execute` 覆盖全部 |
| 5 | AlertingMCPToolset 手写重连 | `process_tool_call` + `tool_error_behavior='retry'` |
| 6 | try/except: pass 吞 L3 错误 | logging.warning + 飞书告警 |
| 7 | 初始化三步易忘 scan | `SkillsCapability.__post_init__` |
| 8 | 无 auto-reload | `auto_reload=True` |
| 9 | 每 skill 一工具不扩展 | 4 参数化标准工具 |
| 10 | load_skill 返回裸 Markdown | 结构化 XML |
| 11 | 无资源/脚本按需加载 | read_skill_resource + run_skill_script |
| 12 | 名称无校验 | SKILL_NAME_PATTERN + 保留字 |
| 13 | split("---", 2) 截断 | 逐行扫描 |
| 14 | 无资源/脚本发现 | rglob + symlink guard |
| 15 | as_pydantic_ai_tools() 死代码 | 删除 |

## 6. pydantic-ai 2.0 Tool 注册最佳实践

### 6.1 三种注册方式的选择决策树

pydantic-ai 2.0 提供了三种工具注册渠道，各有适用场景：

```
需要动态发现工具（工具列表不是编译时确定的）？
  ├── 是 → 用 toolsets 或 capabilities
  │   ├── 工具来自 MCP 服务器？ → MCPToolset
  │   ├── 工具来自文件系统/注册表？ → FunctionToolset（手写子类）
  │   └── 需要生命周期钩子（before_run, after_tool_execute 等）？ → AbstractCapability
  └── 否 → 用 tools 或 tool_plain
      ├── 需要文档字符串自动生成描述？ → agent.tool 装饰器
      └── 函数已存在，不加装饰器？ → agent.tool_plain
```

**规则**：

```
纯 Python 函数工具    → agent.tool / agent.tool_plain
动态工具集（Skill）    → capabilities=[YourCapability()]
MCP 远程工具          → toolsets=[MCPToolset(...)]
```

### 6.2 Agent 构造：`tools` vs `toolsets` vs `capabilities`

```python
agent = Agent(
    model=model,

    # 方式 1: tools — 编译时已知的静态工具
    tools=[my_tool_fn, another_tool_fn],

    # 方式 2: toolsets — 需要按 run 动态生成工具的场景
    # （MCPToolset、FunctionToolset 子类）
    toolsets=[MCPToolset(...)],

    # 方式 3: capabilities — 需要生命周期钩子的场景
    # （也是最推荐的方式，功能超集）
    capabilities=[SkillsCapability(directories=['./skills'])],

    # 全局工具配置
    tool_timeout=60.0,       # 所有工具的超时上限
    retries=3,               # 默认重试次数
)
```

三者的关系：`capabilities` > `toolsets` > `tools`。同一个工具如果在多个 channel 注册，capability 优先。

### 6.3 `AbstractCapability`：被低估的利器

这是 fastapi 项目当前最需要关注的 API。`AbstractCapability` 提供 ~30 个生命周期钩子，远不止"包装 toolset"：

```python
@dataclass
class YourCapability(AbstractCapability):

    # ── 工具注册 ──
    def get_toolset(self) -> FunctionToolset | None:
        """返回工具集（必须实现之一）"""
        return self._toolset

    def get_native_tools(self) -> list:  # 或这个（二个选一）
        """返回原生工具函数列表"""
        return [my_tool_1, my_tool_2]

    # ── 指令注入 ──
    def get_instructions(self) -> AgentInstructions | None:
        """返回要注入到 system prompt 的指令"""
        return None  # 或者 toolset.get_instructions() 已经处理

    # ── 工具生命周期（最有价值的钩子）──
    async def before_tool_execute(self, ctx, tool_name, args):
        """工具执行前：校验参数、记录审计日志、注入上下文"""
        pass

    async def after_tool_execute(self, ctx, tool_name, args, result, error):
        """工具执行后：成本统计、异常告警、结果缓存"""
        if error:
            await send_alert(f"Tool {tool_name} failed: {error}")

    async def on_tool_execute_error(self, ctx, tool_name, args, error):
        """工具执行异常：决定是否重试 / 降级 / 告警"""
        pass

    # ── Agent 运行生命周期 ──
    async def before_run(self, ctx, run_context):
        """每个 run 开始前：初始化资源"""
        pass

    async def after_run(self, ctx, run_context, result, error):
        """每个 run 结束后：清理资源、持久化统计"""
        pass

    async def wrap_run(self, ctx, run_context, next_step):
        """完全控制 run 的执行流（AOP 切面）"""
        result = await next_step()
        # 自定义逻辑...
        return result

    # ── 模型请求生命周期 ──
    async def before_model_request(self, ctx, messages):
        """模型请求前：修改 messages、添加 few-shot 示例"""
        pass

    async def after_model_request(self, ctx, messages, response, error):
        """模型请求后：检查 token 消耗、缓存响应"""
        pass

    # ── 更多钩子 ──
    # before_node_run / after_node_run
    # before_tool_validate / after_tool_validate
    # before_output_process / after_output_process
    # on_model_request_error / on_node_run_error
    # wrap_model_request / wrap_tool_execute / wrap_run_event_stream
```

**关键洞察**：fastapi 当前的 `make_guarded`（告警包装）、`AlertingMCPToolset.call_tool`（重连告警）、token 监控 — 这三件事都可以统一到**一个 `Capability` 的钩子**里，而不是分散在 3 个不同的机制中。

### 6.4 `MCPToolset` 最佳实践

#### 6.4.1 内置能力 vs 自定义覆盖

`MCPToolset` 在 2.0 已经内置了很多 fastapi 手动实现的功能：

| fastapi 手动实现 | pydantic-ai 2.0 内置 | 建议 |
|------|---------------------|------|
| `AlertingMCPToolset.call_tool()` 异常捕获 | `tool_error_behavior='retry'`（默认已是） | 去掉外层 try/except，用 `on_tool_execute_error` 钩子 |
| `_reconnect()` 重连逻辑 | `max_retries` + `transport` 层的自动重连 | 评估是否仍然需要手动重连 |
| `call_tool()` 覆盖 | `process_tool_call` 回调 | 改为 `process_tool_call` 参数 |

#### 6.4.2 推荐构造方式

```python
from pydantic_ai.mcp import MCPToolset, StreamableHttpTransport

# 创建 transport（连接层）
transport = StreamableHttpTransport(
    url="https://mcp-server.example.com/mcp",
    headers={"Authorization": f"Bearer {api_key}"},
)

# 创建 MCPToolset（内置重试 + 错误处理）
mcp_toolset = MCPToolset(
    transport,
    tool_error_behavior='retry',     # 默认值：工具调用失败自动重试
    max_retries=3,                   # 最大重试次数
    cache_tools=True,                # 缓存工具列表（避免每次 run 重新获取）
    init_timeout=30.0,               # 初始化超时
)

# 把告警/日志逻辑放到 Capability 钩子中，不要覆盖 call_tool
```

#### 6.4.3 `process_tool_call` 回调模式

如果确实需要在工具调用前后做处理，用 `process_tool_call` 而不是覆盖 `call_tool`：

```python
async def process_tool(
    tool_name: str,
    args: dict,
    ctx: RunContext,
    call_next: Callable[[], Awaitable[Any]],
) -> Any:
    """在 MCP tool 调用前后注入逻辑"""
    logger.info(f"MCP tool: {tool_name}({args})")
    try:
        result = await call_next()  # 实际的 MCP 调用
        logger.info(f"MCP tool {tool_name} 完成")
        return result
    except Exception as e:
        logger.error(f"MCP tool {tool_name} 失败: {e}")
        await send_feishu_alert(f"MCP tool {tool_name} 异常: {e}")
        raise

mcp_toolset = MCPToolset(transport, process_tool_call=process_tool)
```

### 6.5 `FunctionToolset` 最佳实践

#### 6.5.1 `@self.tool` 装饰器完整参数

```python
class MyToolset(FunctionToolset):

    @self.tool(
        name='custom_name',                  # 覆盖函数名
        retries=3,                           # 单独设置重试次数
        timeout=10.0,                        # 单独设置超时
        requires_approval=True,              # 需要人工审批
        sequential=True,                     # 串行执行（不能并行）
        strict=True,                         # 严格模式（参数必须完全匹配 schema）
        docstring_format='google',           # docstring 格式
        require_parameter_descriptions=True,  # 要求每个参数有描述
    )
    async def my_tool(ctx: RunContext[MyDeps], param1: str, param2: int = 0) -> str:
        """工具的简短描述（作为 tool description）。

        Args:
            param1: 参数1的说明（LLM 会看到）。
            param2: 参数2的说明，默认0。
        """
        ...
```

**关键**：`@self.tool` 自动从函数签名提取参数名、类型和默认值，从 docstring 提取 tool description 和参数说明——不需要手动构建 `ToolDefinition`。

#### 6.5.2 `@self.tool_plain`：不强制 `ctx` 参数

当工具函数不需要 `RunContext` 时：

```python
class MyToolset(FunctionToolset):

    @self.tool_plain
    async def get_current_time() -> str:
        """获取当前服务器时间。"""
        return datetime.now().isoformat()
```

**与 fastapi 当前 `agent.tool_plain` 的区别**：`@self.tool_plain` 在 toolset 内部注册，享受 toolset 的生命周期管理；`agent.tool_plain` 注册在 Agent 级别，无法按 toolset 分组。

#### 6.5.3 参数校验：`BeforeValidator` 模式

```python
from pydantic import BeforeValidator
from typing import Annotated

def _coerce_json_to_dict(v: Any) -> dict:
    """LLM 可能传 JSON 字符串，自动转为 dict"""
    if isinstance(v, str):
        return json.loads(v)
    return v

class MyToolset(FunctionToolset):

    @self.tool
    async def run_script(
        ctx: RunContext,
        script_name: str,
        args: Annotated[dict | None, BeforeValidator(_coerce_json_to_dict)] = None,
    ) -> str:
        ...
```

`BeforeValidator` 在 pydantic 校验链的最前端运行——比参数类型检查更早。

### 6.6 fastapi 项目：推荐的统一架构

综合以上分析，fastapi-agent-pydanticai 的理想 tool 注册架构：

```python
# ── capability.py ──
@dataclass
class AppCapability(AbstractCapability):
    """聚合所有工具 + MCP + Skill + 生命周期钩子"""

    mcp_configs: list[McpServerConfig]
    skill_directories: list[str | Path]
    auto_reload: bool = False

    _mcp_toolsets: list = field(init=False, repr=False)
    _skill_capability: SkillsCapability = field(init=False, repr=False)

    def __post_init__(self):
        # MCP 工具集
        self._mcp_toolsets = [
            MCPToolset(
                StreamableHttpTransport(url=c.url, headers=c.headers),
                tool_error_behavior='retry',
                max_retries=3,
            )
            for c in self.mcp_configs
        ]
        # 技能工具集
        self._skill_capability = SkillsCapability(
            directories=self.skill_directories,
            auto_reload=self.auto_reload,
        )

    # ── 工具注册（框架自动调用）──
    def get_toolset(self):
        """返回 skill toolset。MCP toolsets 直接挂到 Agent 上。"""
        return self._skill_capability.get_toolset()

    def get_native_tools(self):
        """编译时已知的普通工具"""
        return [current_time, ask_human]

    # ── 统一的 MCP 错误处理（替代 AlertingMCPToolset）──
    async def before_tool_execute(self, ctx, tool_name, args):
        logger.debug(f"Tool 开始: {tool_name}")

    async def after_tool_execute(self, ctx, tool_name, args, result, error):
        if error:
            logger.error(f"Tool 失败: {tool_name}", exc_info=error)
            await send_feishu_alert(f"Tool '{tool_name}' 异常: {error}")

    async def on_tool_execute_error(self, ctx, tool_name, args, error):
        """统一的重试/告警入口"""
        if 'not connected' in str(error).lower():
            return 'retry'  # 告诉框架重试
        return 'error'      # 让框架按默认行为处理

    # ── 统一的资源生命周期 ──
    async def before_run(self, ctx, run_context):
        """Agent run 开始前"""
        pass

    async def after_run(self, ctx, run_context, result, error):
        """Agent run 结束后，持久化 cost、token 统计等"""
        await save_token_usage(...)


# ── model_client.py ──
def init_agent() -> Agent:
    capability = AppCapability(
        mcp_configs=[
            McpServerConfig(name='WebSearch', url=..., api_key=...),
            McpServerConfig(name='amap-maps', url=..., api_key=...),
            McpServerConfig(name='image-search', url=..., api_key=...),
        ],
        skill_directories=['./skills'],
        auto_reload=True,
    )
    agent = Agent(
        model=model,
        capabilities=[capability],
        toolsets=[*capability._mcp_toolsets],  # MCP 走 toolsets channel
        tool_timeout=30.0,
        retries=2,
    )
    return agent
```

**这个架构消除了当前的三个不一致**：

| 当前 | 统一后 |
|------|--------|
| `make_guarded` 覆盖 `tool_plain` | `after_tool_execute` 钩子统一覆盖所有工具 |
| `AlertingMCPToolset.call_tool` 覆盖 | `process_tool_call` 回调 + `on_tool_execute_error` 钩子 |
| Skill 工具无错误处理 | 同享 `after_tool_execute` 告警 |
| Token cost 在 `worker_tasks.save_token_usage` | 在 `after_run` 钩子中同步完成 |

### 6.7 Tool 注册反模式（应避免）

| 反模式 | 为什么不好 | 正确做法 |
|--------|-----------|---------|
| 覆盖 `MCPToolset.call_tool()` | 破坏框架的 retry/error behavior 逻辑 | 用 `process_tool_call` 回调 |
| 手动构建 `ToolDefinition` dict | 与函数签名不同步 | 用 `@self.tool` 装饰器 |
| 手写 `AbstractToolset.get_tools()` | 80 行胶水代码 | 继承 `FunctionToolset` |
| `try/except: pass` 吞错误 | 生产排查不到问题 | 用 `on_tool_execute_error` 钩子 |
| `agent.tool_plain` 注册一切 | 工具无法分组管理 | 按功能分组到不同 toolset/capability |
| 全局单例 Agent 不可变 | 无法按场景切换配置 | 单例 OK，但通过 capability 参数化 |
| 每个 MCP server 手写连接+重试 | 重复代码 | `MCPToolset` 内置 `tool_error_behavior='retry'` |
