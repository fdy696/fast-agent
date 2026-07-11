# Token 成本追踪 — 实现计划

> 基于 pydantic-ai 2.0 内置的 `usage_limits` + `agent_result.usage()` 机制

## 当前状态

- `models/token_usage.py` — TokenUsage 模型 ✅
- `repositories/token_usage.py` — TokenUsageRepository ✅（`insert` + `sum_cost_today`）
- `core/config.py` — `AGENT_MODEL` / `COMPRESS_MODEL` / `TOKEN_COST_DAILY_LIMIT` ✅
- pydantic-ai `agent_result.usage()` 暴露 `input_tokens` / `output_tokens` / `cache_read_tokens` ✅

**缺失**：
- `cost.py` 定价表 + 计算函数
- `agent_service.py` 的 `on_complete` 里没有任何 token 记录
- Agent 未配置 `usage_limits`

## 任务

### Task 1: 新建 `src/agent/cost.py`

移植 `fastapi-agent-api/src/agent/cost.py`，去掉 `track_daily`（改用 DB 的 `sum_cost_today`）：

```python
"""Token 费用计算 — DeepSeek 定价."""

PRICING = {
    "deepseek-v4-pro":   {"input": 2.0, "cached": 0.145, "output": 8.0},
    "deepseek-v4-flash": {"input": 0.27, "cached": 0.014, "output": 1.1},
}

def calculate(*, prompt_tokens=0, cached_tokens=0, completion_tokens=0, model="deepseek-v4-pro") -> float:
    p = PRICING.get(model, PRICING["deepseek-v4-pro"])
    uncached = max(0, prompt_tokens - cached_tokens)
    return (uncached * p["input"] + cached_tokens * p["cached"] + completion_tokens * p["output"]) / 1_000_000
```

### Task 2: 修改 `agent_service.py`

**2a.** import `cost` 和 `TokenUsageRepository`（已有 `asyncio`）

**2b.** `on_complete` 回调末尾加 usage 写入（fire-and-forget）：

```python
# on_complete 内，现有持久化逻辑之后：
usage = agent_result.usage()
if usage.input_tokens > 0 or usage.output_tokens > 0:
    asyncio.create_task(
        self._save_token_usage(
            conv_id=conv.id, user_id=user_id, run_id=run_id,
            usage=usage,
        )
    )
```

**2c.** 新增 `_save_token_usage` 方法：

```python
async def _save_token_usage(self, *, conv_id, user_id, run_id, usage) -> None:
    from db.session import AsyncSessionLocal
    from agent.cost import calculate
    from core.config import settings

    cached = usage.cache_read_tokens + usage.cache_write_tokens
    try:
        async with AsyncSessionLocal() as db:
            repo = TokenUsageRepository(db)
            await repo.insert(
                user_id=user_id,
                conversation_id=conv_id,
                run_id=run_id,
                model=settings.AGENT_MODEL,
                prompt_tokens=usage.input_tokens,
                cached_tokens=cached,
                completion_tokens=usage.output_tokens,
                total_tokens=usage.input_tokens + usage.output_tokens,
                cost=calculate(
                    prompt_tokens=usage.input_tokens,
                    cached_tokens=cached,
                    completion_tokens=usage.output_tokens,
                    model=settings.AGENT_MODEL,
                ),
            )
            # 每日超限告警
            today_cost = await repo.sum_cost_today(user_id=user_id)
            if today_cost > settings.TOKEN_COST_DAILY_LIMIT:
                from utils.feishu import send_alert
                await send_alert(
                    "Token 费用超限",
                    f"用户: {user_id}\n今日累计: ${today_cost:.4f}\n阈值: ${settings.TOKEN_COST_DAILY_LIMIT:.2f}",
                )
    except Exception as exc:
        logger.warning("Failed to save token usage: %s", exc)
```

### Task 3（可选）: `model_client.py` 注入 `usage_limits`

```python
from pydantic_ai.usage import UsageLimits

agent = Agent(
    model,
    toolsets=get_mcp_toolsets(),
    usage_limits=UsageLimits(
        request_limit=settings.AGENT_MAX_STEPS,
        total_tokens_limit=200_000,
    ),
)
```

## 数据流

```
Agent LLM 请求 → pydantic-ai 自动累计 usage → on_complete 回调
  → agent_result.usage() → input_tokens / output_tokens / cache_tokens
  → calculate() → $ 费用
  → TokenUsageRepository.insert() → PG token_usage 表
  → sum_cost_today() → 超限 → send_feishu_alert()
```

与手写版对齐，但 `usage` 来源从手算变成 pydantic-ai 内置。

## 改动文件

| 文件 | 操作 | 行数 |
|------|------|------|
| `src/agent/cost.py` | **新建** | +20 |
| `src/services/agent_service.py` | 修改 | +10 |
| `src/agent/model_client.py` | 可选 | +3 |
