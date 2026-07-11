# Token 超限自动续写 — 实现计划

> 单轮限制接近模型上限，超限自动续写，用户无感
> 预算控制交给 L2 单人单天维度

## 设计理念

```
单轮: AGENT_TOKEN_LIMIT = 100K（接近 DeepSeek 128K 窗口）
  → 超限 = 模型输出空间不够 → 自动续写 → 用户无感

单天: TOKEN_COST_DAILY_LIMIT（L2 定时检查）
  → 超限 = 花多了 → 飞书提醒
```

单轮限制不是为了省钱——是匹配模型输出上限。预算控制在另一个维度。

## 方案

```
用户提问 → run_stream #1 → 跑到 100K 被截断
  → 静默 run_stream #2（模型从断点续写）
  → 跑到 100K 又被截断
  → 静默 run_stream #3
  → 完整输出 → on_complete → 保存
```

## 代码

### `src/services/agent_service.py` — 续写循环

```python
from pydantic_ai.usage import UsageLimitExceeded

MAX_CONTINUES = 2   # 最多续写 2 次（首轮 + 续写×2 = 最长 300K token）

partial_output: list[str] = []
continue_count = 0

while True:
    try:
        async for event in adapter.run_stream(
            message_history=message_history,
            conversation_id=session_id,
            instructions=system_prompt,
            on_complete=on_complete if continue_count == 0 else None,
            usage_limits=UsageLimits(
                request_limit=settings.AGENT_MAX_STEPS,
                total_tokens_limit=settings.AGENT_TOKEN_LIMIT,
            ),
        ):
            if isinstance(event, TextMessageContentEvent):
                partial_output.append(event.delta)
            yield event
        break  # 正常完成

    except UsageLimitExceeded:
        continue_count += 1
        if continue_count > MAX_CONTINUES:
            yield RunErrorEvent(message="回复超长", code="token_limit_exceeded")
            break
        # 静默续写，用户无感
```

## 改动文件

| 文件 | 操作 |
|------|------|
| `src/services/agent_service.py` | event_stream 加续写循环 |
