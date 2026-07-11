# FastAPI 中 `asyncio.create_task` 的四个致命问题——以及为什么你需要一个任务队列

> 从零搭建一个 AI Agent 后端模板时，我们用了 5 处 `asyncio.create_task`。后来发现这是个坑。

---

如果你在用 FastAPI 写异步后端，大概率写过这样的代码：

```python
async def on_complete(agent_result):
    # 业务逻辑...
    asyncio.create_task(save_token_usage(user_id, cost))
    asyncio.create_task(generate_title(conv_id, first_msg))
```

这个模式的学名叫 fire-and-forget——"扔出去就不管了"。它的好处是极简：一行代码，不阻塞当前请求，用户立即拿到响应。很多项目的第一个版本都是靠它跑起来的，包括我们这个 AI Agent 模板。

问题在于，随着功能增多，项目里攒了 5 个 `create_task`。直到某天同事问了一个问题："进程挂了这些任务会丢吗？"

答案让她沉默了。

## 五个 `create_task`，五个坑

我们梳理了一下项目中所有使用 fire-and-forget 的地方：

| 位置 | 操作 | 丢了会怎样 |
|------|------|-----------|
| Token 费用入库 | 每轮对话写审计记录 | 少一条，月底对账差几毛 |
| 会话标题生成 | 调 LLM 生成标题 | 标题永远是 "New Chat" |
| Reasoning 步骤保存 | 保存模型思考过程 | 刷新后"已思考"区块消失 |
| MCP 工具失败告警 | 飞书通知 | 没人知道外部服务挂了 |
| 本地工具失败告警 | 同上 | 同上 |

单个看问题都不大——毕竟`create_task`的本质是"尽力而为"。但合在一起，每次进程重启都是一场小型数据灾难。

更关键的是，这是一个**模板项目**——用户拿回去是要当脚手架用的。如果脚手架里的异步操作不保证持久化，用户在生产环境踩坑只是时间问题。

## 模板项目的设计悖论

这里有个矛盾：**模板不能太复杂，但也不能把"反正你会自己改"当借口。**

加一个 Redis 任务队列不是过度设计——它是一种"正确的默认配置"。
用户拿回去直接可用，如果想换 RabbitMQ 或 Kafka，换一行配置就行。
但如果不加，用户甚至不知道这里需要改——等到线上踩了坑才回来翻源码。

## `create_task` vs 任务队列

| | `create_task` | 任务队列 |
|---|---|---|
| 进程崩溃 | **数据丢失** | Redis 持久化，Worker 重启后继续消费 |
| 失败处理 | 无 | 自动重试，耗尽入死信 |
| 可观测性 | 无 | Dashboard 看队列积压 |
| 资源隔离 | Web 进程内 | 独立 Worker 进程 |
| 代码量 | 1 行 | 2 行（`enqueue` + 函数定义） |
| 新基础设施 | 零 | Redis（项目已有） |

多写几行代码，换持久化、重试、可观测、资源隔离——这个 tradeoff 在模板项目里显然是值得的。

有一类操作确实不需要队列——返回静态文件、设置 Cookie、清理不重要的缓存。判断标准很简单：**这个操作失败了要不要重试？丢掉能不能接受？** 两个都是"是"就用队列。

## 选型：为什么是 SAQ

Python 生态的任务队列很多。对于"已有 Redis 的 FastAPI 项目"这个场景，可选项迅速收敛到两个：

| | SAQ | arq |
|---|---|---|
| 依赖 | Redis | Redis |
| API | `queue.enqueue` + 函数列表 | `queue.enqueue_job` + decorator |
| async-native | ✅ | ✅ |
| Dashboard | 内置 | 需额外配置 |

选 SAQ。arq 同样是优秀的库（pydantic 作者写的），但 SAQ 更轻——Worker 就是普通 async 函数，不需要装饰器或类继承，一个函数列表即可。Dashboard 一行命令启动，零配置。

## 为什么不用 PostgreSQL 做队列

在很多生产环境中，PostgreSQL 的出箱模式（Outbox Pattern）是一个可靠的方案。但对我们模板项目来说：

1. **不值得**。最坏情况丢一条多花几毛钱或标题回退到默认值，不是银行转账。
2. **不直观**。`SELECT ... FOR UPDATE SKIP LOCKED` 对新手不友好。
3. **引入新概念**。出箱表需要迁移、清理策略、监控长尾消息。

Redis + SAQ 是最小的认知增量。

## SAQ 集成指南

SAQ 和 FastAPI 的集成分两步：创建队列实例，定义 Worker 函数。

### 1. 创建队列实例

```python
# src/utils/queue.py
import saq
from core.config import settings

queue = saq.Queue.from_url(settings.REDIS_URL)
```

一个模块级全局 `queue` 对象。Web 进程用它投递任务，Worker 进程用它消费。

### 2. 定义 Worker 函数

```python
# src/agent/worker_tasks.py
from db.session import AsyncSessionLocal

async def save_token_usage(ctx, *, conversation_id, user_id, run_id, **usage_data):
    from repositories.token_usage import TokenUsageRepository
    async with AsyncSessionLocal() as db:
        await TokenUsageRepository(db).insert(
            user_id=user_id, conversation_id=conversation_id,
            run_id=run_id, **usage_data,
        )

async def generate_title(ctx, *, conv_id, first_msg):
    from repositories.conversation import ConversationRepository
    from agent.prompts import generate_title as do_gen
    title = await do_gen(first_msg)
    async with AsyncSessionLocal() as db:
        conv = await ConversationRepository(db).get_by_id(conv_id)
        if conv:
            await ConversationRepository(db).update_title(conv, title)

FUNCTIONS = [save_token_usage, generate_title, ...]
```

SAQ 的函数列表模式：Worker 启动时加载 `FUNCTIONS`，函数名即任务名。每个 Worker 函数第一个参数是 `ctx`（SAQ 注入的上下文），业务参数通过关键字传参。

### 3. Worker 独立部署

```bash
# Web 进程（不变）
uvicorn src:app --workers 4

# Worker 进程（新增）
saq src.utils.queue:queue --functions src.agent.worker_tasks:FUNCTIONS --workers 2
```

## 如何使用 SAQ

SAQ 的 API 分两端：producer（投递）和 consumer（消费）。

### Producer 端：投递任务

```python
from utils.queue import queue

# 基础投递
await queue.enqueue("save_token_usage", user_id=42, cost=0.03)

# 延迟投递（30 秒后执行）
await queue.enqueue("send_reminder", user_id=42, schedule=30)
```

`enqueue()` 返回一个 `Job` 对象，立即返回，不等待执行结果。

### Consumer 端：处理任务

Worker 进程消费 `FUNCTIONS` 列表中的函数。SAQ 自动处理成功→删除、失败→重试、耗尽→死信。

### 观察队列状态

```python
info = await queue.info()
print(f"Queued: {info.queued}, Failed: {info.failed}")
```

也可以启动 Dashboard：`saq src.utils.queue:queue --web --port 8081`。

## 代码实现

### 第一步：安装 SAQ

```bash
pip install saq
```

### 第二步：创建队列

```python
# src/utils/queue.py
import saq
from core.config import settings

queue = saq.Queue.from_url(settings.REDIS_URL)
```

### 第三步：定义 Worker 函数

```python
# src/agent/worker_tasks.py
from db.session import AsyncSessionLocal


async def save_token_usage(ctx, *, conversation_id, user_id, run_id, **usage_data):
    from repositories.token_usage import TokenUsageRepository
    from agent.cost import calculate
    from core.config import settings

    prompt_tokens = usage_data.get("prompt_tokens", 0)
    cached_tokens = usage_data.get("cached_tokens", 0)
    completion_tokens = usage_data.get("completion_tokens", 0)
    cost = calculate(input_tokens=prompt_tokens, cached_tokens=cached_tokens,
                     output_tokens=completion_tokens, model=settings.AGENT_MODEL)

    async with AsyncSessionLocal() as db:
        await TokenUsageRepository(db).insert(
            user_id=user_id, conversation_id=conversation_id,
            run_id=run_id, model=settings.AGENT_MODEL,
            prompt_tokens=prompt_tokens, cached_tokens=cached_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            cost=cost,
        )


async def generate_title(ctx, *, conv_id, first_msg):
    from repositories.conversation import ConversationRepository
    from agent.prompts import generate_title as do_generate_title

    title = await do_generate_title(first_msg)
    async with AsyncSessionLocal() as db:
        conv = await ConversationRepository(db).get_by_id(conv_id)
        if conv:
            await ConversationRepository(db).update_title(conv, title)


async def save_reasoning(ctx, *, run_id, text):
    from repositories import AgentStepRepository

    async with AsyncSessionLocal() as db:
        await AgentStepRepository(db).insert(
            run_id=run_id, step_index=0, category="reasoning",
            type="model", name="reasoning", output={"content": text},
            status="succeeded",
        )


async def send_feishu_alert(ctx, *, title, content):
    from utils.feishu import send_alert
    await send_alert(title, content)


FUNCTIONS = [
    save_token_usage,
    generate_title,
    save_reasoning,
    send_feishu_alert,
]
```

四个 Worker 函数，一个 `FUNCTIONS` 列表。设计选择：

- **第一个参数 `ctx`**——SAQ 自动注入的任务上下文，不需要在 `enqueue` 时传递
- **延迟导入**——每个函数内部 `from db.session import AsyncSessionLocal`，避免 Worker 启动时就去连数据库
- **MCP 和本地工具告警合为一个**—— `send_feishu_alert`。两个 source 走同一个函数

### 第四步：替换 `create_task`

5 处替换，模式完全相同：

```python
from utils.queue import queue

# Token 入库
await queue.enqueue("save_token_usage", conversation_id=conv.id,
                    user_id=user_id, run_id=run_id, **usage_data)

# 标题生成
await queue.enqueue("generate_title", conv_id=conv.id, first_msg=user_content)

# Reasoning 保存
await queue.enqueue("save_reasoning", run_id=run_id, text=reasoning_text[0])

# 告警（mcp.py + tool_guard.py 两处）
await queue.enqueue("send_feishu_alert", title=title, content=content)
```

### 第五步：启动 Worker

```bash
# Web 进程（不变）
uvicorn src:app --workers 4

# Worker 进程（新增）
saq src.utils.queue:queue --functions src.agent.worker_tasks:FUNCTIONS --workers 2
```

Web 和 Worker 独立部署、独立扩缩容。

## 多 Worker 的并发语义

```
Web-1 ──┐
Web-2 ──┤── queue.enqueue() ──→ Redis ──→ Worker-1 ── task()
         │                      (LPUSH)      Worker-2 ── task()
         │                             (BRPOPLPUSH，互斥)
```

SAQ 底层用 Redis `BRPOPLPUSH`——原子地弹出一条消息并推入处理中列表，同一条消息不可能被两个 Worker 同时取到。

SAQ 是 at-least-once 语义——Worker 崩溃时任务自动重新入队。对模板项目来说，重复执行影响极小：Token 入库有 `run_id` UNIQUE 约束，标题 UPDATE 天然幂等，飞书告警多一条消息不歧义。

## 不改动的部分

| 组件 | 原因 |
|------|------|
| L2 定时告警 (`token_monitor.py`) | 本来就是独立协程，不走请求链路 |
| L1 实时截断 (`UsageLimits`) | pydantic-ai 内置，请求路径上同步拦截 |

## 总结

如果你的项目用了三个以上的 `create_task`，就值得引入一个轻量任务队列。

不是因为性能，不是因为架构，是因为**数据完整性**——任务不应该因为运气不好就被忘记。SAQ + Redis 是最小的认知增量：5 处替换，两个新建文件，一行安装命令，不需要新基础设施。

这就是模板项目应该提供的正确默认值。
