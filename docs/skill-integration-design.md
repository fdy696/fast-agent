# 不要把所有东西塞进 System Prompt——给你的 AI Agent 设计一套 Skill 按需加载系统

> 受 Claude Code 的 Skill 机制启发，用 pydantic-ai 的 `AbstractToolset` 实现了一套 L0→L1→L2→L3 的渐进式能力注入。

---

在现实世界的 AI Agent 后端开发中，你迟早会遇到一个问题：system prompt 太长了。

你的旅行助手需要规划行程、查天气、搜图片、做地图——每个能力都有几千字的指令和模板。如果把所有能力的指引都写进 system prompt，每次 LLM 调用都要花几千 token 传递一屏幕的上下文，而其中大部分跟当前请求毫无关系。

## 为什么大模型需要 Skill

大语言模型懂莎士比亚，懂量子力学，懂税法——但它不懂你的业务。

不是智商不够。是训练数据里没有你的工作流。模型不知道"规划北京三日游"在你的产品里意味着先确认信息、再并行调天气和攻略、最后按特定模板输出。它不知道输出格式里"季节性风险"字段什么时候该填、什么时候该空。它也不知道你接了五个 MCP 工具，哪个先用哪个后用。

这些不是通用智能能解决的——**是只有你知道的领域知识**。

你可能会说：那我写到 system prompt 里不就行了？对。那 system prompt 就会变成这样（见下一节）。Skill 的本质不是优化 prompt，而是把**你的领域知识结构化地注入模型**——让模型从"什么都知道一点"变成"这个领域的事我能按你说的做"。

## 为什么单体 System Prompt 行不通

花三分钟看看你的 system prompt。如果它长这样：

```
你是一个专业的 AI 旅行助手，主要提供旅游规划、行程安排、
景点推荐和旅行建议。

【行程规划规范】
第一步：确认目的地、天数、出行日期、出发地……
第二步：并行调用天气查询、攻略搜索、图片搜索……
第三步：按以下模板输出行程……

【天气查询规范】
当用户询问天气时，先获取城市经纬度……
……（省略 2000 字）
```

你已经遇到了三个实际问题：

**1. Token 浪费。** 用户说"今天北京热吗"，需要看天气预报——但 system prompt 里硬塞了 3000 字的行程规划规范和景点推荐模板。这些跟当前请求毫无关系，但每次 LLM 调用都要传。

**2. 维护困难。** 当你想更新"行程规划"的指令时——比如从四步流程改成五步——你改的是 system prompt 中间的一段文字。没有版本控制，没有边界，不敢太大改。

**3. 上下文窗口竞争。** LLM 的上下文是有限的资源。system prompt 里每多 1000 token，对话历史就少 1000 token。如果多轮对话进行到第 10 轮，你会发现模型已经开始"忘记"前面的内容——因为 system prompt 占了太多位置。

这些都是**单体 system prompt**的典型问题。解决方案是把单体拆成模块——每个能力一个独立文件，模型按需加载。

Claude Code 解决这个问题的方式是用 Skill 文件——按需加载的指令集。模型先看到一份"目录"，判断自己需要什么能力，然后主动调工具获取详情。这篇文章记录了我们用 pydantic-ai 复现这套机制的过程。

## L0/L1/L2/L3：四层渐进式加载

先从核心设计讲起。我们把 Skill 的加载过程拆成四层：

```
L0: 文件系统扫描
    skills/travel-planner/SKILL.md → 解析 YAML 前页 → SkillMeta{name, description, body}
    启动时一次性完成，结果常驻内存

L1: 能力清单（目录）
    "可用能力:
     - travel-planner: 多日旅游行程规划与攻略"
    → 注入 system prompt，模型随时可见。一行一个 skill，几十 token

L2: 技能正文（详情）
    模型判断需要 travel-planner → 调用 load_travel-planner 工具
    → 返回 SKILL.md 正文（~1000 字工作流指令）

L3: 参考模板（附件）
    SKILL.md 的 auto_load_references: [itinerary-template.md]
    → load 时自动内联，模型拿到工作流指令 + 具体输出模板
```

关键设计：L1 永远在 system prompt 里（几十 token），L2 和 L3 只有模型主动调用工具时才加载（几千 token）。99% 的请求是闲聊或简单查询，不会走到 L2，省掉大量无用上下文。

## pydantic-ai 的集成点：AbstractToolset

pydantic-ai 提供了一个专门设计用于这种场景的扩展接口：`AbstractToolset`。实现它之后，Agent 会自动在合适的时机调用你的方法：

```python
import re
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.messages import InstructionPart

class SkillToolset(AbstractToolset):

    def __init__(self, registry):
        self.registry = registry
        self._loaders = {}
        self._tool_to_skill = {}  # tool_name → skill_name 正向映射

    @property
    def id(self) -> str | None:
        return None

    async def get_instructions(self, ctx):
        meta = self.registry.build_skill_metadata()
        if not meta or not meta.strip():
            return []
        # L1 元指令：告诉模型用完后自觉遗忘旧技能的 L2/L3 细节
        hint = (
            "\n\n当用户切换话题或当前技能不再适用时：\n"
            "- 不再引用之前技能的指令\n"
            "- 如需新技能，先调用 load_<name> 获取规则\n"
            "- 忘记旧技能的具体约束，只保留一般能力"
        )
        return [InstructionPart(content=meta + hint)]

    async def get_tools(self, ctx):
        tools = {}
        for name in self.registry.skills:
            tool_name = f"load_{re.sub(r'[^a-zA-Z0-9_]', '_', name)}"
            self._loaders[name] = _make_loader(self.registry, name)
            self._tool_to_skill[tool_name] = name
            tools[tool_name] = ToolsetTool(
                toolset=self,
                tool_def=ToolDefinition(
                    name=tool_name,
                    description=f"加载 '{name}' 能力的完整指令"),
                max_retries=1,
                args_validator=SchemaValidator({"type": "object", "properties": {}}),
            )
        return tools

    async def call_tool(self, name, tool_args, ctx, tool):
        # 查表定位原始 skill 名，调用对应 loader
        return await self._loaders[self._tool_to_skill[name]]()
```

`get_instructions()` 负责 L1——返回的 `InstructionPart` 追加到 system prompt，末尾的元指令引导模型在切换话题时自觉遗忘旧技能的 L2/L3 细节。`get_tools()` + `call_tool()` 负责 L2/L3——为每个 skill 注册工具，LLM 调用时触发加载。`_tool_to_skill` 字典记录工具名到原始 skill 名的正向映射，避免字符串反向解析出错。

Agent 创建时注入：

```python
agent = Agent(
    model,
    toolsets=[
        *get_mcp_toolsets(),             # MCP：天气、搜索、地图
        SkillToolset(registry),          # Skill：按需加载的能力指令
    ],
    usage_limits=UsageLimits(
        request_limit=settings.AGENT_MAX_STEPS,
        total_tokens_limit=settings.AGENT_TOKEN_LIMIT,
    ),
)
```

MCP 工具和 Skill 工具统一走 `toolsets` 参数，pydantic-ai 一视同仁。

## SKILL.md 文件格式

每个 Skill 目录下有一个 `SKILL.md`，YAML 前页定义元数据，正文是指令：

```markdown
---
name: travel-planner
description: 多日旅游行程规划与攻略
auto_load_references:
  - references/itinerary-template.md
---

## 行程规划

**角色**: 你现在以专业旅行规划师身份回复用户。

### 第一步：确认信息
逐一确认 destination、days、date、departure。

### 第二步：获取数据
并行调用 MCP 工具收集天气、攻略、图片、坐标。

### 第三步：输出行程
严格按 `references/itinerary-template.md` 结构输出。
```

`description` 字段出现在 L1 目录，正文是 L2 内容，`auto_load_references` 指向 L3 模板。

## 工作流示例

用户说"帮我规划北京三日游"：

```
1. LLM 收到 system prompt，其中包含 L1:
   "## 可用能力
    - **travel-planner**: 多日旅游行程规划与攻略"

2. LLM 判断需要 travel-planner → 调用 load_travel-planner 工具

3. SkillToolset 返回 L2（SKILL.md 正文）+ L3（itinerary-template.md）
   总共约 2000 token 附加上下文

4. LLM 按指令流程执行:
   → 确认信息 → 调 MCP 工具获取数据 → 按模板输出行程
```

关键点：L2+L3 只在 LLM 主动请求时才注入。闲聊类请求永远看不到那 2000 token 的指令。

## 为什么不用装饰器注册

pydantic-ai 的 `@agent.tool_plain` 装饰器也能注册工具，但这里不适合——因为我们需要的是**运行时动态注册**。Skill 目录可能在容器启动后才挂载，数量不固定，描述文本不固定。`AbstractToolset` 在每次 run 时调用 `get_tools()`，天然支持这种场景。

## 模型如何知道该调用 load 工具

这是整个设计中最容易被忽略的一点，也是最优雅的部分：

L1 目录是自然语言，不是结构化 JSON Schema。LLM 读到"travel-planner: 多日旅游行程规划与攻略"时，它自己判断"用户说要规划北京三日游，我应该用这个"。没有关键词匹配、没有规则引擎、没有显式的"如果是旅游则激活"的业务逻辑。

这是 LLM 非常适合做的事——语义匹配。你的工作只是把目录写得清楚。

## 技能用完后：自觉遗忘

Claude Code 的系统提示里有一条精妙的"元指令"：

> 当你完成一个任务或用户切换话题时，应当忘记之前使用的技能细节，只保留一般能力。如果新任务又需要某个技能，再重新读取它的说明文件。

受此启发，我们在 L1 能力清单末尾追加了同样的提示：

```python
hint = (
    "\n\n当用户切换话题或当前技能不再适用时：\n"
    "- 不再引用之前技能的指令\n"
    "- 如需新技能，先调用 load_<name> 获取规则\n"
    "- 忘记旧技能的具体约束，只保留一般能力"
)
return [InstructionPart(content=meta + hint)]
```

这不是代码逻辑——是提示工程。模型自带注意力机制，加上这条引导，切换话题后旧技能的具体指令就被忽略了。不需要显式的 delete/unload API。

同时，L2/L3 本身是工具调用结果，存在对话历史里。随着对话继续，token 预算自然淘汰旧内容。两层保护：元指令告诉你该忘，上下文窗口帮你执行忘。

注意元指令让模型忘记的是 **L2/L3 的技能细节**（工作流步骤），不是 **L1 的目录**。L1 是 system prompt 的一部分，每次请求重新注入，模型永远知道自己有哪些能力可用——只是不会带着上次的详细步骤去处理新请求。

## 后续方向

两个思考：

1. **自动触发**——Claude Code 支持 hook 机制，在特定关键词出现时自动激活 skill。你可以加一个 `PostToolUse` hook：当模型调用了 `maps_geo` 这类工具时，自动加载旅行规划 skill。不需要模型主动判断。

2. **跨请求状态**——当前每次请求都要重新调用 `load_<name>` 获取指令。如果同一个 Skill 在多轮对话中频繁使用，可以在内存里缓存"该会话已激活的 skill"列表，后续请求自动注入 L2，省一次 tool call。

## 总结

不要把所有指令塞进 system prompt。给模型一份目录，让它自己去书架上取书。

pydantic-ai 的 `AbstractToolset` 就是为了这种场景设计的——它是一个完美的挂载点，让你可以在不侵入 Agent 主流程的情况下，注入动态的能力清单和工具。加上 YAML 前页的 SKILL.md 文件格式，新人只需要写一个 markdown 文件，就能为自己的 AI 应用添加新能力。
