# Pydantic AI 入门篇：第一个 Agent

> ✅ 本文档所有代码可直接运行。面向零基础学习者。

## 本章你将学到
- 创建一个能回复中文的 Agent
- 使用 `agent.run()` 进行非流式调用
- 使用 `agent.run_stream()` 实现打字效果
- 理解 `model`、`instructions`、user prompt 的关系
- 识别并避免最常见的 3 个坑

---

## 1. Pydantic AI 是什么

> **本章目标**：理解 Pydantic AI 是什么、为什么用它
> **预计时间**：3 分钟

Pydantic AI 是一个基于 Pydantic 的 LLM Agent 框架。它的核心理念是：**用 Python 类型系统驱动 LLM 交互**。

- 你定义函数签名和类型注解，Pydantic AI 自动生成 LLM 能理解的 schema
- Agent 内置工具调用、流式输出、重试、状态管理等生产级特性
- 抽象了模型差异：同一个代码，换 model/provider 就能换底层 LLM

简单说：Pydantic AI = 类型安全的 LLM Agent 框架。

## 本章练习

1. 用自己的话向同事解释 Pydantic AI 和直接调 OpenAI API 的区别
2. 列出 Pydantic AI 的三个核心优势

## 验收标准

- [ ] 你能说出 Pydantic AI 的核心定位（类型安全、框架内置工具/流式/重试）
- [ ] 你能区分 Agent、model、instructions 这三者的角色

## 2. 第一个非流式 Agent

> **本章目标**：写出并运行第一个非流式 Agent 调用
> **预计时间**：10 分钟

先从最简单的非流式调用开始。`agent.run()` 等待完整回复后一次性返回。

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

# 3. 非流式调用
async def main():
    result = await agent.run("介绍一下 Python 的 asyncio")
    print(result.data)

asyncio.run(main())
```

**发生了什么：**

```
用户消息 "介绍一下 Python 的 asyncio"
  │
  ▼
Agent.run()
  ├── 组装 messages:
  │     [SystemPrompt(instructions), UserPrompt("介绍一下 Python 的 asyncio")]
  │
  ├── 调用 LLM（非流式）
  │     POST /v1/chat/completions  {stream: false}
  │
  └── 返回 result.data（完整文本）
```

**关键点**：`result.data` 包含 LLM 的完整回复。非流式调用适合不需要实时显示的批处理场景（如数据处理、自动化任务）。

## 本章练习

1. 把示例中的模型从 deepseek-chat 换成 gpt-4o-mini（只需改动模型名和 api_key）
2. 让 Agent 用英文回复（修改 instructions）
3. 运行一次，观察 `result.data` 的格式

## 验收标准

- [ ] 你能自己写出一个 `agent.run()` 的最小调用
- [ ] 你能解释 `result.data` 里是什么

## 常见错误

1. **API key 写错**：检查 key 是否有效、base_url 是否准确
2. **模型名写错**：`OpenAIChatModel("deepseek-chat", ...)` 中的模型名是 provider 侧的，不是你的代号
3. **忘写 `asyncio.run(main())`**：异步函数必须通过 `asyncio.run` 或 `await` 执行

## 3. 流式输出 run_stream

> **本章目标**：用 `run_stream()` 实现逐字输出效果
> **预计时间**：8 分钟

流式输出可以逐字显示 LLM 的回复——就像 ChatGPT 的打字效果。适合聊天类应用。

**从非流式升级到流式**：把 `agent.run()` 换成 `agent.run_stream()`，并用 `async for` 逐块读取。

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
    
    print(result.usage)
    print(result)

asyncio.run(main())
```

**发生了什么：**

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

## 本章练习

1. 把第 2 节的非流式代码改成流式版本（只改 3 行代码）
2. 去掉 `flush=True` 运行，观察终端行为差异
3. 在流结束前打印 `result.data`，确认它是 `None`

## 验收标准

- [ ] 你能写出 `async with agent.run_stream() as result: async for event in result.stream()` 的完整结构
- [ ] 你知道流式过程中和结束后分别如何访问内容

## 常见错误

1. **在流结束前访问 `result.data`**：返回 `None`，不是 bug 而是设计
2. **忘记 `flush=True`**：终端可能缓冲，看不到实时打字效果
3. **误以为 `run_stream` 返回完整字符串**：它是迭代器，需要 `async for` 逐块读取

## 4. instructions、user prompt、model 的关系

> **本章目标**：清晰理解三大组件各自的作用
> **预计时间**：5 分钟

这三者是 Agent 的三大基础组件：

```
                    ┌───────────────────────┐
                    │     Agent 对象         │
                    │                       │
                    │  model = DeepSeek      │  ← 决定"谁来回答"
                    │  instructions = "..."  │  ← 决定"怎么回答"
                    │                       │
                    └───────┬───────────────┘
                            │
            ┌───────────────┼───────────────┐
            │               │               │
            ▼               ▼               ▼
      SystemPrompt    UserPrompt        LLM 调用
      (instructions)  (用户输入)       (通过 model)
```

### model

`model` 决定了 Agent 使用哪个 LLM 提供商和模型。通过 `OpenAIChatModel`（兼容 OpenAI / DeepSeek / 任何 OpenAI 兼容 API）或 `GeminiModel`/`AnthropicModel` 等创建。

```python
# OpenAI
model = OpenAIChatModel("gpt-4o", provider=OpenAIProvider(api_key="sk-..."))

# DeepSeek（OpenAI 兼容 API）
model = OpenAIChatModel("deepseek-chat", provider=OpenAIProvider(
    openai_client=AsyncOpenAI(base_url="https://api.deepseek.com/v1")
))

# Gemini
from pydantic_ai.models.gemini import GeminiModel
model = GeminiModel("gemini-2.0-flash", provider=...)  # 需要 Google AI API key
```

### instructions

`instructions` 是 Agent 的系统指令（system prompt），告诉 LLM 如何表现：

```python
agent = Agent(
    model=model,
    instructions="You are a helpful assistant. Reply in Chinese.",
    # 可选：额外的 system prompt
    system_prompt="当前日期: 2026-07-02",
)
```

### user prompt

user prompt 是每次调用时传入的用户消息：

```python
result = await agent.run("介绍一下 Python")
```

### 三者协作示例

```python
agent = Agent(
    model=model,
    instructions="你是旅游助手。回答要简洁，不超过 3 句话。",
)

result1 = await agent.run("推荐北京景点")
result2 = await agent.run("推荐上海景点")  # 同个 Agent，不同问题
```

instructions 只需设置一次，user prompt 每次调用都不同。

## 本章练习

1. 写一个 Agent：instructions 设"你是旅游助手，回答不超过 3 句话"，然后分别问"推荐北京景点"和"推荐上海景点"
2. 交换 instructions 和 user prompt 的角色：如果把景点信息写在 instructions 里，把问题作为 user prompt，会发生什么？

## 验收标准

- [ ] 你能准确说出 model / instructions / user prompt 各自的作用
- [ ] 你能解释为什么 instructions 只需设置一次而 user prompt 每次不同

## 常见错误

1. **把 instructions 当 user prompt 用**：instructions 是系统级设定，user prompt 是每次对话的输入
2. **instructions 写得模糊**：LLM 按你的描述行事，"帮用户" 不如 "你是一个中文旅游助手，每次回答不超过 3 句话"
3. **误以为 model 参数只是模型名**：它同时决定了 provider（谁提供服务）和具体的模型版本

## 5. 常见坑与调试

> **本章目标**：掌握最常见错误的识别和调试方法
> **预计时间**：5 分钟

### 坑 1：在流结束前访问 `result.data`

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

### 坑 2：忘记 `flush=True`

```python
# ❌ 终端可能缓冲，看不到实时输出
print(event.delta, end='')

# ✅ 强制刷新
print(event.delta, end='', flush=True)
```

### 坑 3：用错模型名

`OpenAIChatModel` 的第一个参数是**模型提供商的模型名**（如 `"deepseek-chat"`、`"gpt-4o"`），不是你自定义的代号。Provider 的 `base_url` 决定了请求发往哪里。

### 调试技巧

如果 Agent 行为不符合预期，首先检查：
1. **instructions 是否清晰**：LLM 按你的描述行事，模糊的 instructions 导致随机行为
2. **API key 和 base_url 是否正确**：错误的 API key 会立即报错，错误的 base_url 会返回奇怪结果
3. **模型名是否拼写正确**：各提供商的模型名不同，官网文档为准确来源

## 本章练习

1. 故意写错一个 API key，运行程序观察报错信息
2. 在流式代码中，在 `async for` 之前打印 `result.data`，确认是 `None`
3. 尝试把 instructions 从中文改成英文，观察 Agent 回复语言变化

## 验收标准

- [ ] 你能独立排查常见的 Agent 运行错误（API key / 模型名 / 异步调用）
- [ ] 你能快速定位并修复流式输出中的问题
