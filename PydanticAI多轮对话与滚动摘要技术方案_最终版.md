# PydanticAI 多轮对话与滚动摘要技术方案（最终版）

> 文档版本：3.0  
> 基线日期：2026-07-10  
> 技术栈：FastAPI、PydanticAI、SQLAlchemy Async、PostgreSQL、Redis  
> 数据表约束：仅使用 `chat_session`、`chat_message` 两张业务表

---

## 1. 目标

实现一套主线清晰、可直接落地的多轮对话方案：

- 使用 PydanticAI 原生 `ModelMessage` 保存历史；
- 支持多会话、多轮上下文恢复；
- 支持 HTTP 流式响应；
- 前端发送新消息时中断上一条流；
- 正常 Run 和中断 Run 都持久化；
- 中断 Run 只用于展示，不进入模型上下文和摘要；
- 摘要后置执行，不阻塞主对话；
- 支持多实例下的幂等写入和摘要互斥；
- 仅使用 `chat_session`、`chat_message` 两张业务表。

---

## 2. 核心设计

### 2.1 一条 `chat_message` 保存一次 Agent Run

```text
chat_message.message_list
=
result.new_messages()
```

一次 Run 可能包含：

```text
ModelRequest(UserPromptPart)
ModelResponse(ToolCallPart)
ModelRequest(ToolReturnPart)
ModelResponse(TextPart)
```

因此不能将历史降级为传统的 `role/content` 结构。

### 2.2 原始消息是唯一真相

```text
chat_message.message_list
```

是完整历史的唯一真相来源。

以下内容均为派生数据：

- `chat_session.summary_message_list`；
- 前端展示用 `role/content`；
- Langfuse / Logfire Trace；
- 业务日志。

### 2.3 不增加重复的状态字段

PydanticAI 原生消息已经携带生命周期状态：

```text
complete
incomplete
interrupted
```

数据库不再增加 `status` 或 `state` 字段，避免两套状态不一致。

### 2.4 两类历史分开使用

```text
前端展示：全部 Run
模型上下文：仅完整 Run
滚动摘要：仅完整 Run
```

中断响应可能包含残缺工具调用，因此原样保存用于展示和审计，但不重新传给 `message_history`。

### 2.5 使用 `chat_message.id` 排序和推进摘要游标

删除：

```text
turn_no
last_turn_no
SELECT FOR UPDATE 分配轮次
```

统一使用：

```text
chat_message.id
```

承担：

- 数据库持久化顺序；
- 历史稳定排序；
- 滚动摘要游标。

---

## 3. 最终执行链路

```mermaid
flowchart TD
    A[用户发送新消息] --> B[前端 Abort 上一条请求]
    B --> C[立即发起新请求]

    D[旧请求所在实例] --> E[检测客户端断开]
    E --> F[result.cancel]
    F --> G[保存 interrupted Run]

    C --> H[读取旧摘要和完整历史]
    H --> I[主 Agent 流式执行]
    I --> J[正常结束或被中断]
    J --> K[统一保存 new_messages]
    K --> L[响应结束后尝试摘要]
    L --> M{获取 summary_lock?}
    M -- 否 --> N[跳过]
    M -- 是 --> O{完整未摘要 Run 达到阈值?}
    O -- 否 --> P[结束]
    O -- 是 --> Q[生成并保存新摘要]
```

主原则：

```text
前端负责断开旧 HTTP 流；
后端负责取消 PydanticAI Run；
正常和中断 Run 都原样保存；
中断 Run 不参与后续模型上下文；
摘要是可重建的后置派生缓存。
```

---

## 4. 数据库设计

### 4.1 `chat_session`

```sql
CREATE TABLE chat_session (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    title VARCHAR(255),

    -- 当前滚动摘要，保存为合法 ModelMessage 数组
    summary_message_list JSONB,

    -- 摘要已覆盖到的最后一个 chat_message.id
    summarized_through_message_id BIGINT NOT NULL DEFAULT 0,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX ix_chat_session_user_id
    ON chat_session(user_id);
```

### 4.2 `chat_message`

```sql
CREATE TABLE chat_message (
    id BIGSERIAL PRIMARY KEY,

    session_id UUID NOT NULL
        REFERENCES chat_session(id)
        ON DELETE CASCADE,

    -- PydanticAI 当前 Agent Run ID
    run_id VARCHAR(64) NOT NULL,

    -- result.new_messages() 的原生 JSON
    message_list JSONB NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_chat_message_run
        UNIQUE(session_id, run_id)
);

CREATE INDEX ix_chat_message_session_id
    ON chat_message(session_id, id);
```

说明：

- `id` 是稳定排序字段，也是摘要游标；
- `created_at` 仅用于展示时间；
- `UNIQUE(session_id, run_id)` 用于幂等提交；
- 不维护 `turn_no` 和 `last_turn_no`。

---

## 5. SQLAlchemy 模型

```python
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class ChatSession(Base):
    __tablename__ = "chat_session"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    title: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    summary_message_list: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    summarized_through_message_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ChatMessage(Base):
    __tablename__ = "chat_message"

    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "run_id",
            name="uq_chat_message_run",
        ),
        Index(
            "ix_chat_message_session_id",
            "session_id",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_session.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    message_list: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
```

---

## 6. 运行时结构

```python
from dataclasses import dataclass

from pydantic_ai import ModelMessage, ModelRequest, ModelResponse


@dataclass(frozen=True)
class MessageBatch:
    message_id: int
    messages: list[ModelMessage]


def is_reusable_run(messages: list[ModelMessage]) -> bool:
    """只有完整 Run 才能进入模型上下文和摘要。"""
    lifecycle_messages = [
        message
        for message in messages
        if isinstance(message, (ModelRequest, ModelResponse))
    ]

    return bool(lifecycle_messages) and all(
        message.state == "complete"
        for message in lifecycle_messages
    )


def flatten_batches(
    batches: list[MessageBatch],
) -> list[ModelMessage]:
    return [
        message
        for batch in batches
        for message in batch.messages
    ]
```

不要只判断是否存在 `interrupted`。

完整 Run 的标准是：

```text
所有 ModelRequest / ModelResponse 的 state 都是 complete
```

这样也能排除异常留下的 `incomplete` 消息。

---

## 7. Repository：读取模型上下文

模型上下文由以下两部分组成：

```text
当前滚动摘要
+
summarized_through_message_id 之后的完整 Run
```

```python
from uuid import UUID

from pydantic_ai import ModelMessage, ModelMessagesTypeAdapter
from sqlalchemy import select


class ChatRepository:
    async def load_context(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
    ) -> tuple[list[ModelMessage], list[MessageBatch]]:
        session = await self._db.scalar(
            select(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.user_id == user_id,
            )
        )
        if session is None:
            raise LookupError("chat session not found")

        summary_messages: list[ModelMessage] = []
        if session.summary_message_list:
            summary_messages = ModelMessagesTypeAdapter.validate_python(
                session.summary_message_list
            )

        rows = (
            await self._db.execute(
                select(ChatMessage)
                .where(
                    ChatMessage.session_id == session_id,
                    ChatMessage.id
                    > session.summarized_through_message_id,
                )
                .order_by(ChatMessage.id)
            )
        ).scalars().all()

        batches: list[MessageBatch] = []

        for row in rows:
            messages = ModelMessagesTypeAdapter.validate_python(
                row.message_list
            )

            if not is_reusable_run(messages):
                continue

            batches.append(
                MessageBatch(
                    message_id=row.id,
                    messages=messages,
                )
            )

        return summary_messages, batches
```

Repository 不把 ORM 对象返回给 Service。

---

## 8. Repository：幂等保存 Run

正常和中断 Run 使用同一个保存方法。

```python
from uuid import UUID

from pydantic_ai import ModelMessage
from pydantic_core import to_jsonable_python
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert


class ChatRepository:
    async def commit_run(
        self,
        *,
        session_id: UUID,
        run_id: str,
        new_messages: list[ModelMessage],
    ) -> int:
        stmt = (
            insert(ChatMessage)
            .values(
                session_id=session_id,
                run_id=run_id,
                message_list=to_jsonable_python(new_messages),
            )
            .on_conflict_do_nothing(
                index_elements=["session_id", "run_id"],
            )
            .returning(ChatMessage.id)
        )

        message_id = await self._db.scalar(stmt)

        if message_id is None:
            message_id = await self._db.scalar(
                select(ChatMessage.id).where(
                    ChatMessage.session_id == session_id,
                    ChatMessage.run_id == run_id,
                )
            )

        await self._db.commit()

        if message_id is None:
            raise RuntimeError("failed to persist chat run")

        return message_id
```

该实现具有真正的幂等语义：

```text
首次提交：插入并返回 id
重复提交：不重复插入，返回已有 id
```

不再需要：

- 锁住 `chat_session`；
- 计算 `last_turn_no + 1`；
- 处理 `turn_no` 冲突。

---

## 9. ChatService：流式对话和取消

```python
import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import Request


class ChatService:
    def __init__(
        self,
        repository: ChatRepository,
    ) -> None:
        self._repository = repository

    async def stream_chat(
        self,
        *,
        request: Request,
        session_id: UUID,
        user_id: UUID,
        prompt: str,
    ) -> AsyncIterator[str]:
        summary_messages, batches = await self._repository.load_context(
            session_id=session_id,
            user_id=user_id,
        )

        message_history = [
            *summary_messages,
            *flatten_batches(batches),
        ]

        result = None

        try:
            async with chat_agent.run_stream(
                prompt,
                message_history=message_history,
                conversation_id=str(session_id),
            ) as result:
                async for output in result.stream_output(
                    debounce_by=0.05
                ):
                    if await request.is_disconnected():
                        await result.cancel()
                        break

                    yield str(output)

        except asyncio.CancelledError:
            # ASGI 直接取消流式生成器时的兜底
            if result is not None and not result.cancelled:
                await result.cancel()
            raise

        finally:
            if result is not None:
                new_messages = result.new_messages()

                if new_messages:
                    await asyncio.shield(
                        self._repository.commit_run(
                            session_id=session_id,
                            run_id=str(result.run_id),
                            new_messages=new_messages,
                        )
                    )
```

保存规则统一：

```text
正常完成：保存 complete 消息
主动取消：保存 interrupted 消息
异常留下非完整消息：原样保存
```

后续是否可复用，由 `is_reusable_run()` 统一判断。

---

## 10. FastAPI 路由

```python
from fastapi import Depends, Request
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask


@router.post("/sessions/{session_id}/messages")
async def send_message(
    request: Request,
    session_id: UUID,
    body: ChatRequest,
    service: ChatService = Depends(get_chat_service),
    summary_service: SummaryService = Depends(get_summary_service),
    current_user: CurrentUser = Depends(get_current_user),
) -> StreamingResponse:
    return StreamingResponse(
        service.stream_chat(
            request=request,
            session_id=session_id,
            user_id=current_user.id,
            prompt=body.content,
        ),
        media_type="text/event-stream",
        background=BackgroundTask(
            summary_service.try_summarize,
            session_id,
        ),
    )
```

摘要后台任务必须：

- 自己创建新的数据库会话；
- 不复用已经结束的请求级 `AsyncSession`；
- 将摘要视为最佳努力任务；
- 任务丢失时由后续请求再次触发。

---

## 11. 前端取消上一条请求

```javascript
let currentController = null;

async function sendMessage(content) {
  currentController?.abort();

  const controller = new AbortController();
  currentController = controller;

  try {
    const response = await fetch(`/sessions/${sessionId}/messages`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ content }),
      signal: controller.signal,
    });

    // 按项目现有方式读取 response.body 流
  } catch (error) {
    if (error.name !== "AbortError") {
      throw error;
    }
  } finally {
    if (currentController === controller) {
      currentController = null;
    }
  }
}
```

取消链路：

```text
AbortController.abort()
→ 浏览器断开旧 HTTP 流
→ request.is_disconnected() 或 CancelledError
→ result.cancel()
→ PydanticAI 记录 interrupted 响应
→ commit_run() 原样保存
```

前端 Abort 只是提供断开信号，真正取消模型流的是后端的 `result.cancel()`。

---

## 12. 前端历史展示

历史查询 API 读取当前会话的全部 `chat_message`：

```sql
SELECT *
FROM chat_message
WHERE session_id = :session_id
ORDER BY id;
```

展示时只做单向投影：

```python
from pydantic_ai import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)


def to_ui_messages(
    messages: list[ModelMessage],
) -> list[dict[str, str]]:
    ui_messages: list[dict[str, str]] = []

    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if (
                    isinstance(part, UserPromptPart)
                    and isinstance(part.content, str)
                ):
                    ui_messages.append({
                        "role": "user",
                        "content": part.content,
                    })

        elif isinstance(message, ModelResponse):
            content = "".join(
                part.content
                for part in message.parts
                if isinstance(part, TextPart)
            )

            if content:
                ui_messages.append({
                    "role": "assistant",
                    "content": content,
                })

    return ui_messages
```

该投影只用于展示，不能反向用于恢复 PydanticAI 历史。

---

## 13. 滚动摘要

### 13.1 摘要策略

```python
SUMMARY_TRIGGER_RUNS = 10
KEEP_RECENT_RUNS = 4
```

规则：

```text
完整且未摘要的 Run 少于 10 个：
    不摘要

完整且未摘要的 Run 达到 10 个：
    旧摘要 + 较旧完整 Run → 新摘要
    最近 4 个完整 Run 保留原文
```

中断或非完整 Run：

- 不计入摘要阈值；
- 不进入摘要 Agent；
- 不删除，仍可用于前端展示。

### 13.2 摘要上下文

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class SummaryContext:
    previous_summary: list[ModelMessage]
    old_cursor: int
    unsummarized_batches: list[MessageBatch]
```

读取逻辑与 `load_context()` 一致：

```text
读取 id > summarized_through_message_id 的 Run
→ 只保留 is_reusable_run() 为 True 的 Run
→ 按 id 升序返回
```

### 13.3 调用摘要 Agent

```python
async def summarize_history(
    *,
    previous_summary: list[ModelMessage],
    old_messages: list[ModelMessage],
) -> list[ModelMessage]:
    result = await summary_agent.run(
        "请根据以上历史更新滚动摘要，只输出新摘要。",
        message_history=[
            *previous_summary,
            *old_messages,
        ],
    )

    summary_text = str(result.output).strip()
    if not summary_text:
        raise ValueError("summary agent returned empty output")

    return build_summary_messages(summary_text)
```

### 13.4 摘要任务

```python
async def try_summarize(
    self,
    session_id: UUID,
) -> None:
    if not await self._summary_lock.try_acquire(session_id):
        return

    try:
        ctx = await self._repository.load_summary_context(session_id)

        if len(ctx.unsummarized_batches) < SUMMARY_TRIGGER_RUNS:
            return

        old_batches = ctx.unsummarized_batches[:-KEEP_RECENT_RUNS]
        if not old_batches:
            return

        new_summary = await summarize_history(
            previous_summary=ctx.previous_summary,
            old_messages=flatten_batches(old_batches),
        )

        await self._repository.save_summary(
            session_id=session_id,
            new_summary_messages=new_summary,
            old_cursor=ctx.old_cursor,
            new_cursor=old_batches[-1].message_id,
        )
    except Exception:
        logger.exception("chat summary failed", extra={
            "session_id": str(session_id),
        })
    finally:
        await self._summary_lock.release(session_id)
```

### 13.5 保存摘要

```python
from pydantic_core import to_jsonable_python
from sqlalchemy import update


async def save_summary(
    self,
    *,
    session_id: UUID,
    new_summary_messages: list[ModelMessage],
    old_cursor: int,
    new_cursor: int,
) -> bool:
    result = await self._db.execute(
        update(ChatSession)
        .where(
            ChatSession.id == session_id,
            ChatSession.summarized_through_message_id == old_cursor,
        )
        .values(
            summary_message_list=to_jsonable_python(
                new_summary_messages
            ),
            summarized_through_message_id=new_cursor,
        )
    )

    await self._db.commit()
    return result.rowcount > 0
```

旧游标条件用于防止较旧的摘要结果覆盖较新的摘要。

---

## 14. 并发控制

### 14.1 主对话不使用分布式会话锁

当前主流程不引入：

- `session_lock`；
- Redis cancel 标记；
- `active_run_id`；
- Task 注册表；
- WebSocket；
- 消息队列。

原因：

- 前端直接中断旧 HTTP 流；
- 中断 Run 本来就不会进入下一轮模型上下文；
- `chat_message.id` 由数据库自动生成；
- `commit_run()` 使用唯一约束实现幂等。

### 14.2 摘要使用一把 Redis 锁

```text
chat:summary:{session_id}
```

规则：

```text
获取成功：执行摘要
获取失败：已有摘要任务，直接跳过
```

摘要锁只防止重复消耗，不参与主聊天流程。

---

## 15. 故障策略

| 场景 | 处理 |
|---|---|
| 主 Agent 正常完成 | 保存完整 `new_messages()` |
| 客户端中断 | `result.cancel()`，保存 interrupted 消息 |
| 中断消息含残缺工具调用 | 原样保存，但不进入模型上下文和摘要 |
| 主 Agent 在取得 `result` 前失败 | 不保存本轮 Run |
| 消息重复提交 | `ON CONFLICT DO NOTHING`，返回已有 `id` |
| 数据库提交失败 | 回滚并记录 `run_id` |
| 摘要失败 | 不更新摘要，后续请求再次尝试 |
| 摘要任务丢失 | 后续请求再次触发 |
| 摘要并发 | `summary_lock` 跳过重复任务，旧游标校验防止覆盖 |
| 历史 JSON 损坏 | 记录错误并停止复用，不猜测消息角色 |

---

## 16. 必须通过的测试

### 16.1 序列化 Round-trip

```python
payload = result.new_messages_json()
messages = ModelMessagesTypeAdapter.validate_json(payload)
assert messages == result.new_messages()
```

### 16.2 两轮上下文

```text
第一轮：我叫张三
第二轮：我叫什么？
```

第二轮必须能够从数据库历史回答“张三”。

### 16.3 取消保存

客户端中断后验证：

- 调用了 `result.cancel()`；
- 保存了 `result.new_messages()`；
- 最终响应包含 `state="interrupted"`；
- 前端历史能够展示中断 Run。

### 16.4 历史隔离

下一轮 `message_history`：

- 只包含 `is_reusable_run() == True` 的 Run；
- 不包含 interrupted Run；
- 不包含 incomplete Run；
- 仍包含此前完整 Run。

### 16.5 工具链

正常完成的工具 Run 必须完整保存：

```text
ToolCallPart
ToolReturnPart
TextPart
```

中断工具 Run 原样保存，但不能进入模型上下文。

### 16.6 幂等提交

同一个 `(session_id, run_id)` 重复提交：

- 只产生一条数据库记录；
- 两次调用返回同一个 `chat_message.id`。

### 16.7 摘要

达到阈值后：

- 只摘要完整 Run；
- 保留最近 4 个完整 Run 原文；
- 游标推进到最后一个被摘要的 `chat_message.id`；
- 不删除任何原始消息；
- 摘要失败不影响聊天。

---

## 17. 项目目录

```text
src/
├── api/
│   └── chat.py
├── services/
│   ├── chat_service.py
│   └── summary_service.py
├── repositories/
│   └── chat_repository.py
├── models/
│   └── chat.py
├── agent/
│   ├── chat_agent.py
│   └── summary_agent.py
├── schemas/
│   └── chat.py
└── infrastructure/
    └── summary_lock.py

tests/
├── unit/
│   ├── test_message_state.py
│   ├── test_message_serialization.py
│   └── test_summary_service.py
└── integration/
    ├── test_multiturn_chat.py
    ├── test_stream_cancellation.py
    ├── test_idempotent_commit.py
    └── test_summary_rollup.py
```

---

## 18. 明确不采用

- 不按 `role/content` 保存；
- 不增加重复的 `status/state` 字段；
- 不使用 `turn_no` 和 `last_turn_no`；
- 不在 `ProcessHistory` 中执行滚动摘要；
- 不清理后再保存中断工具调用；
- 不使用主会话分布式锁；
- 不使用 Redis cancel 标记；
- 不使用 WebSocket；
- 不使用消息队列；
- 不删除原始历史。

---

## 19. 最终原则

> **一条 `chat_message` 保存一次 PydanticAI Run；`chat_message.id` 同时作为持久化顺序和摘要游标；正常和中断 Run 都原样保存；前端展示全部历史，模型上下文和摘要只使用完整 Run；前端负责断开旧流，后端负责调用 `result.cancel()`；摘要后置执行，失败不影响主对话。**

---

## 20. 官方参考

- PydanticAI — Messages and chat history  
  https://pydantic.dev/docs/ai/core-concepts/message-history/

- PydanticAI — Cancelling streams and interrupted history  
  https://pydantic.dev/docs/ai/core-concepts/output/#cancelling-streams

- PydanticAI — StreamedRunResult API  
  https://pydantic.dev/docs/ai/api/pydantic-ai/result/

- PydanticAI — Messages API  
  https://pydantic.dev/docs/ai/api/pydantic-ai/messages/

- Starlette — Request disconnect detection  
  https://www.starlette.io/requests/

- Starlette — Background tasks  
  https://www.starlette.io/background/
