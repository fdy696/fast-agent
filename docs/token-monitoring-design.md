# FastAPI + pydantic-ai 的 Token 费用监控体系

> 50 行代码 · 三层防御 · 能扛 100K DAU
> 
> 在 AI 应用开发的早期阶段，Token 费用往往不是首要关注点。但随着日活增长到 10 万级别，每天数千次 LLM 调用带来的费用累积就变成了一个必须正视的问题——而且是"越早上"收益越大的基础设施问题。

本文记录了一种三层防御的 Token 费用监控体系——从实时止损到定时告警再到事后审计——每一层解决一个特定层面的问题，代码量控制在 50 行以内。

## 需要解决什么问题

把需求拆开看：

| 需求 | 本质 | 实时性要求 | 手段 | 阻塞流式？ |
|------|------|-----------|------|-----------|
| 不想单轮跑飞 | 单次对话失控及时止损 | **秒级** | L1 `UsageLimits` 硬拦截 | **是**（必须） |
| 不想单人刷爆 | 单用户单日预算硬顶 | **实时** | Redis 原子累加 + 请求入口检查 | **是**（Redis O(1) 微秒级） |
| 花多了知道 | 全局预算兜底通知 | **分钟级** | 定时查 + 飞书告警 | **否**（独立 coroutine） |
| 知道谁花的 | 按用户追溯明细 | **天级** | SQL 查询 | **否**（队列异步写入） |
| 不能拖慢回复 | 监控链路与业务路径隔离 | **实时** | — | — |

三个需求三个时间尺度，对应的手段完全不同。很多方案把三者混在一起做实时累加 + 实时告警，结果 Redis、DB 轮番上阵，代码奔着 200 行去。其实搞清楚每一层到底要解决什么，代码自然会瘦下来。

还有一个隐式的核心约束：**监控链路不能阻塞流式返回**。用户等的是 AI 回复，不是你的账单入库。设计上必须保证三层中没有一层会拖慢 SSE 事件流的交付速度。

## 三层防御

```
L1: 秒级止损 ─ pydantic-ai 内置 UsageLimits，单次请求超 200K token 自动终止
    一句话：不花钱比省钱的优先级更高。

L2a: 全局告警 ─ 每 10 分钟查一次 DB，全站超过 $5 就飞书通知
    一条告警，人能看到。

L2b: 单人硬顶 ─ Redis 原子累加单用户单日费用，请求入口检查
    超预算直接拒绝（429），Redis INCRBYFLOAT 微秒级，不阻塞

L3: 审计追溯 ─ 每轮对话经 SAQ 队列异步 INSERT 一条 token_usage
    Worker 独立消费，进程崩溃不丢。想看谁花多了？SQL 一行 GROUP BY。
```

### L1: 单轮硬顶 + 自动续写

```python
agent = Agent(
    model,
    toolsets=get_mcp_toolsets(),
    usage_limits=UsageLimits(
        request_limit=settings.AGENT_MAX_STEPS,
        total_tokens_limit=settings.AGENT_TOKEN_LIMIT,  # 100K, 接近模型上限
    ),
)
```

超限后 `UsageLimitExceeded` → `event_stream` 捕获 → 静默续写（最多 2 次），用户看到完整输出。续写属于模型输出上限场景，不是预算紧缩。

详见：[截断续写计划](./truncation-continue-plan.md)

### L2: 全局告警

这是整个体系最核心的一层——但也是最容易过度设计的一层。

**设计决策**：定时查，不做实时。

原因很简单——$5 的每日限额，早 5 分钟和晚 5 分钟通知没本质区别。但每次对话结束时都去 `SELECT SUM(cost)` 是实打实的数据库压力。100K 日活假设下每天 200 万次对话，如果每次对话结束都查两次 SUM（全局 + 单用户），那就是 400 万次聚合查询——哪怕有索引，这也是完全不必要的开销。

改为每 10 分钟查一次，144 次/天。多 Worker 同时查也没关系——PostgreSQL 的聚合查询在索引覆盖下毫秒级返回，多个 Worker 同时查的瞬时 QPS 也就是 Worker 数量级别，完全不需要 Redis 做累加层。

**去重**：Redis `SETNX`。10 分钟内所有 Worker 都做一次 `SELECT SUM(cost)`，但只有一个 Worker 能抢到 SETNX 锁，只有它去发飞书。一天一把锁，24 小时 TTL 后自动释放。

```python
async def _check() -> None:
    now = datetime.now(timezone.utc)
    dedup_key = f"token_alert:{now.strftime('%Y%m%d')}"

    async with AsyncSessionLocal() as db:
        total = await TokenUsageRepository(db).sum_cost_today_global(current_time=now)

    limit = settings.TOKEN_COST_DAILY_LIMIT
    if total > limit and await redis.set(dedup_key, "1", ex=86400, nx=True):
        await send_alert("Token 费用超限",
                         f"今日全站: ${total:.4f} / 阈值: ${limit:.2f}")
```

时区坑注意：代码中统一使用 `datetime.now(timezone.utc)`，传入 Repository 构建 SQL 的起止时间。不要混用 `date.today()`——它在不同服务器上可能是不同时区，和 Redis key 的 UTC 日期错位。

### L2b: 单人单天硬顶

请求入口用 Redis 原子累加检查单用户当日费用。SAQ Worker 入库后同步更新计数器。

**请求入口检查**（`agent_service.py` `stream_ag_ui` 早期）：

```python
from utils.cache import cache_manager
from datetime import datetime, timezone

today = datetime.now(timezone.utc).strftime("%Y%m%d")
key = f"token:daily:user:{user_id}:{today}"
current = float(await cache_manager._client.get(key) or 0)
if current > settings.TOKEN_COST_PER_USER_DAILY_LIMIT:
    # 注意：精确值由 SAQ Worker 写入，这里读的是近似值
    raise HTTPException(429, "今日用量已超预算，请明天再试")
```

**SAQ Worker 写入**（`worker_tasks.py` `save_token_usage` 末尾）：

```python
today = datetime.now(timezone.utc).strftime("%Y%m%d")
key = f"token:daily:user:{user_id}:{today}"
await redis.incrbyfloat(key, cost)
await redis.expireat(key, int(midnight_utc.timestamp()))
```

### 多实例 / 多 Worker 安全分析

```
Web-1: GET key → 3.2 → < 5.0 → OK → enqueue task
Web-2: GET key → 3.2 → < 5.0 → OK → enqueue task  ← 并发通过
Worker-1: save_token_usage → INCRBYFLOAT +0.8 → 4.0
Worker-2: save_token_usage → INCRBYFLOAT +0.9 → 4.9
  → 两人都通过了检查，但都没有超。因为 Worker 异步写入，计数器有短暂延迟
  → 最坏情况：用户在接近限额时多发几条，总额略微超出（~$1），可接受
```

与定时查 DB 对比——这是**实时拦截**，微秒级 Redis GET，不阻塞请求。

### L3: 审计追溯

每轮对话结束，投递到任务队列异步写入——不阻塞 `on_complete` 返回，进程崩溃不丢数据：

```python
# agent_service.py 的 on_complete 回调：
usage = agent_result.usage()
if usage.input_tokens > 0 or usage.output_tokens > 0:
    await queue.enqueue("save_token_usage", conversation_id=conv.id,
                        user_id=user_id, run_id=run_id, **usage_data)
```

写入经 SAQ 任务队列持久化到 Redis，Worker 进程独立消费。比起 `asyncio.create_task`，进程崩溃后任务不会丢失。

想查谁花多了就查：

```sql
SELECT user_id, SUM(cost) as total
FROM token_usage
WHERE created_at >= '2026-06-29'
GROUP BY user_id
ORDER BY total DESC
LIMIT 20;
```

## 不做的事

明确不做的和做的一样重要：

| 不做 | 原因 |
|------|------|
| Redis 实时累加 | 定时查询够用，不引入额外一致性负担 |
| 单用户告警 | 100K 用户每人超限都告警 → 飞书变垃圾邮件 |
| 内存去重 | 多 Worker 不安全，Redis 一行 `SETNX` 解决 |
| 存储过程 / 物化视图 | PG 裸查询 + 索引已经够快 |

## 完整代码

### `src/agent/token_monitor.py`（新建，30 行）

```python
"""L2 全局费用告警 — 每 10 分钟查 DB，Redis SETNX 去重."""
import asyncio
from datetime import datetime, timezone
from log import logger


async def start() -> None:
    asyncio.create_task(_poll())


async def _poll() -> None:
    while True:
        await asyncio.sleep(600)
        try:
            await _check()
        except Exception as exc:
            logger.warning(f"L2 check failed: {exc}")


async def _check() -> None:
    from db.session import AsyncSessionLocal
    from repositories.token_usage import TokenUsageRepository
    from core.config import settings
    from utils.cache import cache_manager
    from utils.feishu import send_alert

    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y%m%d")
    redis = cache_manager._client
    dedup_key = f"token_alert:{today_str}"

    async with AsyncSessionLocal() as db:
        total = await TokenUsageRepository(db).sum_cost_today_global(current_time=now)

    limit = settings.TOKEN_COST_DAILY_LIMIT
    if total > limit and await redis.set(dedup_key, "1", ex=86400, nx=True):
        try:
            await send_alert(
                "Token 费用超限",
                f"今日全站: ${total:.4f} / 阈值: ${limit:.2f}",
            )
        except Exception:
            pass
```

### `src/repositories/token_usage.py` 新增方法

```python
async def sum_cost_today_global(self, current_time) -> float:
    start = datetime(current_time.year, current_time.month, current_time.day,
                     tzinfo=timezone.utc)
    end = datetime(current_time.year, current_time.month, current_time.day,
                   23, 59, 59, 999999, tzinfo=timezone.utc)
    result = await self.db.execute(
        select(func.coalesce(func.sum(TokenUsage.cost), 0.0)).where(
            TokenUsage.created_at >= start,
            TokenUsage.created_at <= end,
        )
    )
    return float(result.scalar_one())
```

### `src/agent/model_client.py` — L1（+4 行）

```python
from pydantic_ai.usage import UsageLimits

agent = Agent(model, toolsets=get_mcp_toolsets(),
    usage_limits=UsageLimits(
        request_limit=settings.AGENT_MAX_STEPS,
        total_tokens_limit=300_000,  # DeepSeek V4 最大输出 384K，预留 84K 给 system prompt
    ))
```

### `src/core/init_app.py` — 启动（+2 行）

```python
from agent.token_monitor import start
start()
```

## 容量评估

```
100K DAU × 日均 5 轮 × 每轮 4 次 LLM 调用
= 2,000,000 INSERT/天 ≈ 23 writes/s  → PG 零压力
=  144 SUM 查询/天（定时）           → 可忽略不计
=  1 条飞书/天（超过 $5 时）         → 人能看到
```

索引建议：

```sql
CREATE INDEX idx_token_usage_created_at ON token_usage (created_at);
```

## 总结

三层防御的核心理念是"各层做各层的事"：L1 不要替代 L2（硬拦截不要顺便告警），L2 不要替代 L3（定时查不要顺便做实时累加），每层聚焦一个时间尺度的需求。L3 的写入经 SAQ 任务队列异步持久化，保证进程崩溃后审计数据不丢失，同时不在请求路径上阻塞。代码量 50 行，改动 4 个文件，100K DAU 绰绰有余。
