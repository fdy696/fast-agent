# PydanticAI 多轮对话与滚动摘要技术方案（最终版）

> 文档版本：3.2  
> 基线日期：2026-07-11  
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

运行时取得当前 Run 新产生的原生消息：

```python
new_messages: list[ModelMessage] = result.new_messages()
```

写入 PostgreSQL `JSONB` 前转换为 JSON 兼容的 Python 对象：

```python
message_list: list[dict] = to_jsonable_python(new_messages)
```

最终对应关系是：

```text
chat_message.message_list
=
to_jsonable_python(result.new_messages())
```

一次 Run 可能包含：

```text
ModelRequest(UserPromptPart)
ModelResponse(ToolCallPart)
ModelRequest(ToolReturnPart)
ModelResponse(TextPart)
```

因此不能将历史降级为传统的 `role/content` 结构。

`message_list` 不保存以下内容：

- 不只保存 `result.output`，否则会丢失用户输入和工具链；
- 不保存 `result.all_messages()`，否则会把旧历史在每轮重复写入；
- 不自行拼装 `role/content`，否则会丢失 PydanticAI 原生消息类型和状态。

`result.new_messages()` 是 `list[ModelMessage]`；数据库中的 `message_list` 是它序列化后的 `list[dict]`。两者语义相同，但类型不同。

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
    E --> F[停止消费 run_stream_events]
    F --> G[退出 async with 并清理后台 Run]
    G --> H[capture_run_messages 获取部分消息]
    H --> I[保存 interrupted Run]

    C --> J[读取旧摘要和完整历史]
    J --> K[run_stream_events 完整执行 Agent Graph]
    K --> L[拼装最终文本快照]
    L --> M[取得 AgentRunResultEvent]
    M --> N[统一保存 new_messages]
    N --> O[响应结束后尝试摘要]
    O --> P{获取 summary_lock?}
    P -- 否 --> Q[跳过]
    P -- 是 --> R{完整未摘要 Run 达到阈值?}
    R -- 否 --> S[结束]
    R -- 是 --> T[生成并保存新摘要]
```

主原则：

```text
前端负责断开旧 HTTP 流；
后端使用 run_stream_events() 完整执行工具链；
客户端断开时停止消费事件并退出 async with，清理后台 Run；
正常 Run 保存 AgentRunResult.new_messages()；
中断 Run 保存 capture_run_messages() 捕获的当前 Run 消息；
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

    -- to_jsonable_python(result.new_messages()) 生成的原生消息 JSON
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
from pydantic_ai.messages import TextPart


@dataclass(frozen=True)
class MessageBatch:
    message_id: int
    messages: list[ModelMessage]


def is_reusable_run(messages: list[ModelMessage]) -> bool:
    """只有语义完整的文本输出 Run 才能进入上下文和摘要。"""
    lifecycle_messages = [
        message
        for message in messages
        if isinstance(message, (ModelRequest, ModelResponse))
    ]

    if not lifecycle_messages:
        return False

    if not all(
        message.state == "complete"
        for message in lifecycle_messages
    ):
        return False

    # 本方案的 Agent 输出类型为 str。
    # 完整工具链必须以包含非空 TextPart 的 ModelResponse 结束。
    last_message = lifecycle_messages[-1]
    if not isinstance(last_message, ModelResponse):
        return False

    return any(
        isinstance(part, TextPart)
        and bool(part.content.strip())
        for part in last_message.parts
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

不能只判断是否存在 `interrupted`，也不能只判断全部消息的 `state == "complete"`。

对于本方案默认的字符串输出，完整 Run 必须同时满足：

```text
1. 所有 ModelRequest / ModelResponse 的 state 都是 complete；
2. 最后一条生命周期消息是 ModelResponse；
3. 最后一条 ModelResponse 中存在非空 TextPart。
```

第二、三条用于排除以下语义不完整历史：

```text
ModelResponse(ToolCallPart)
→ ModelRequest(ToolReturnPart)
→ 结束
```

这种历史中的每条消息可能都是 `complete`，但工具结果还没有再次发给模型生成最终回答，因此不能进入下一轮上下文或滚动摘要。若以后将 `output_type` 改为结构化类型，应同步替换最后一条消息的终态校验规则。

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

### 8.1 序列化契约

正常和中断 Run 使用同一套序列化规则：

```python
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_core import to_jsonable_python

new_messages = result.new_messages()

# 写入 JSONB：list[ModelMessage] -> list[dict]
message_list = to_jsonable_python(new_messages)

# 从 JSONB 读取：list[dict] -> list[ModelMessage]
restored_messages = ModelMessagesTypeAdapter.validate_python(
    message_list
)
```

必须明确：

```python
serialized = ModelMessagesTypeAdapter.dump_python(
    new_messages,
    mode="json",
)
```

`serialized` 已经是普通 `list`，不能再调用：

```python
serialized.model_dump()  # 错误：list 没有 model_dump()
```

因此数据库保存优先使用：

```python
message_list = to_jsonable_python(result.new_messages())
```

`result.new_messages_json()` 返回 JSON 字节串，更适合文件或网络传输；如需写入 `JSONB`，还要先 `json.loads()`，没有必要多做一次编码和解码。

### 8.2 幂等保存实现

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

## 9. ChatService：完整工具链流式执行和取消

### 9.1 为什么使用 `run_stream_events()`

本方案不再使用 `agent.run_stream()` 作为生产主链路。

`run_stream()` 会将第一个符合 `output_type` 的输出视为最终输出。默认 `output_type=str` 时，如果模型在同一次响应中同时返回：

```text
TextPart（例如“马上帮你查询”）
+
ToolCallPart
```

前面的文本可能先被认定为最终结果，工具即使执行，其返回值也可能不会再次发送给模型，最终历史停在：

```text
ModelResponse(ToolCallPart)
→ ModelRequest(ToolReturnPart)
```

`run_stream_events()` 会让 Agent Graph 运行到完成，并在事件流最后产生：

```text
AgentRunResultEvent(result=AgentRunResult(...))
```

因此能够完整覆盖：

```text
模型请求工具
→ 执行工具
→ 工具结果返回模型
→ 模型生成最终答案
```

### 9.2 原始事件与前端快照

`run_stream_events()` 返回原始事件，需要自行从以下事件拼装文本：

```text
PartStartEvent(TextPart)
PartDeltaEvent(TextPartDelta)
FinalResultEvent
AgentRunResultEvent
```

本方案只向前端发送真正的最终文本，不发送工具调用前的过渡文本：

```text
FinalResultEvent 之前：只缓存文本，不推送
FinalResultEvent 之后：把 TextPart + TextPartDelta 拼成完整快照并推送
AgentRunResultEvent：用 result.output 做最后一次校准
```

前端仍然使用覆盖语义：

```javascript
assistantMessage.content = event.content;
```

禁止：

```javascript
assistantMessage.content += event.content;
```

### 9.3 中断语义

`run_stream_events()` 返回的是事件迭代器，不是 `StreamedRunResult`，因此这里不存在：

```python
await result.cancel()
```

客户端断开时：

```text
停止消费事件
→ 退出 run_stream_events() 的 async with
→ PydanticAI 清理后台 Run 和模型连接
→ capture_run_messages() 取得部分消息
→ 保存当前 run_id 对应的 interrupted / incomplete 消息
```

`capture_run_messages()` 捕获的是完整运行消息列表，可能包含传入的旧历史。因此持久化前必须按最新 `run_id` 过滤，只保存当前 Run，不能把旧历史重复写入数据库。

### 9.4 生产实现

```python
import asyncio
import json
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import Request
from pydantic_ai import (
    AgentRunResultEvent,
    FinalResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPartDelta,
    capture_run_messages,
)
from pydantic_ai.messages import ModelMessage, TextPart


def encode_sse(data: dict[str, str]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def build_text_snapshot(parts: dict[int, str]) -> str:
    return "".join(
        parts[index]
        for index in sorted(parts)
    )


def current_run_messages(
    messages: list[ModelMessage],
) -> tuple[str | None, list[ModelMessage]]:
    """从捕获的完整历史中提取最新一次 Run。"""
    run_id = next(
        (
            message.run_id
            for message in reversed(messages)
            if message.run_id is not None
        ),
        None,
    )

    if run_id is None:
        return None, []

    return run_id, [
        message
        for message in messages
        if message.run_id == run_id
    ]


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

        final_result = None
        captured_messages: list[ModelMessage] = []
        text_parts: dict[int, str] = {}
        final_output_started = False
        previous_snapshot: str | None = None

        try:
            with capture_run_messages() as captured_messages:
                async with chat_agent.run_stream_events(
                    prompt,
                    message_history=message_history,
                    conversation_id=str(session_id),
                ) as events:
                    async for event in events:
                        if await request.is_disconnected():
                            # 停止消费；退出 async with 时清理后台 Run。
                            break

                        if isinstance(event, PartStartEvent):
                            # 每次新的模型响应，part index 从 0 重新开始。
                            if event.index == 0:
                                text_parts.clear()
                                final_output_started = False

                            if isinstance(event.part, TextPart):
                                text_parts[event.index] = event.part.content

                        elif isinstance(event, FinalResultEvent):
                            # PartStartEvent 通常先于 FinalResultEvent，
                            # 因此先缓存，再在这里首次向前端发送。
                            final_output_started = True
                            snapshot = build_text_snapshot(text_parts)

                            if snapshot and snapshot != previous_snapshot:
                                previous_snapshot = snapshot
                                yield encode_sse({
                                    "type": "snapshot",
                                    "content": snapshot,
                                })

                        elif (
                            final_output_started
                            and isinstance(event, PartDeltaEvent)
                            and isinstance(event.delta, TextPartDelta)
                        ):
                            text_parts[event.index] = (
                                text_parts.get(event.index, "")
                                + event.delta.content_delta
                            )
                            snapshot = build_text_snapshot(text_parts)

                            if snapshot != previous_snapshot:
                                previous_snapshot = snapshot
                                yield encode_sse({
                                    "type": "snapshot",
                                    "content": snapshot,
                                })

                        elif isinstance(event, AgentRunResultEvent):
                            final_result = event.result

                            # 使用经过最终校验的 output 校准最后一个快照。
                            final_snapshot = str(final_result.output)
                            if (
                                final_snapshot
                                and final_snapshot != previous_snapshot
                            ):
                                previous_snapshot = final_snapshot
                                yield encode_sse({
                                    "type": "snapshot",
                                    "content": final_snapshot,
                                })

        except asyncio.CancelledError:
            # ASGI 取消会沿 async with 向外传播；上下文负责清理后台 Run。
            raise

        finally:
            if final_result is not None:
                new_messages = final_result.new_messages()
                run_id, _ = current_run_messages(new_messages)
            else:
                # 客户端中断或异常时，捕获部分消息。
                run_id, new_messages = current_run_messages(
                    captured_messages
                )

            if run_id is not None and new_messages:
                await asyncio.shield(
                    self._repository.commit_run(
                        session_id=session_id,
                        run_id=run_id,
                        new_messages=new_messages,
                    )
                )
```

保存规则：

```text
正常完成：保存 AgentRunResultEvent.result.new_messages()
客户端中断：保存 capture_run_messages() 中最新 run_id 的消息
运行异常：有当前 run_id 和部分消息则原样保存
Run 尚未产生任何带 run_id 的消息：不保存
```

流式协议继续统一发送：

```json
{"type": "snapshot", "content": "当前完整最终回答"}
```

该实现不会把工具调用前的“马上帮你查询”等过渡文本当成最终回答推送给前端，也不会把旧 `message_history` 重复写入当前 `chat_message`。

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

    // 按项目现有方式读取 response.body 流。
    // 每个 SSE data 都是：
    // { type: "snapshot", content: "当前完整回答" }
    // 收到后覆盖当前助手消息，不要追加：
    // assistantMessage.content = event.content;
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
→ 停止消费 run_stream_events() 事件
→ 退出 async with，清理后台 Run 和模型连接
→ capture_run_messages() 取得部分历史
→ commit_run() 保存当前 run_id 的消息
```

前端 Abort 只负责断开旧 HTTP 流。后端不再调用 `result.cancel()`；`run_stream_events()` 必须放在 `async with` 中，停止消费或任务取消时依靠上下文退出完成确定性清理。

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
| 客户端中断 | 停止消费事件并退出 `run_stream_events()` 上下文，保存捕获的当前 Run 消息 |
| 中断消息含残缺工具调用 | 原样保存，但不进入模型上下文和摘要 |
| 主 Agent 在 `AgentRunResultEvent` 前失败 | 若已产生当前 `run_id` 和部分消息则原样保存，否则不保存 |
| 消息重复提交 | `ON CONFLICT DO NOTHING`，返回已有 `id` |
| 数据库提交失败 | 回滚并记录 `run_id` |
| 摘要失败 | 不更新摘要，后续请求再次尝试 |
| 摘要任务丢失 | 后续请求再次触发 |
| 摘要并发 | `summary_lock` 跳过重复任务，旧游标校验防止覆盖 |
| 历史 JSON 损坏 | 记录错误并停止复用，不猜测消息角色 |

---

## 16. 必须通过的测试

### 16.1 JSONB 序列化 Round-trip

```python
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_core import to_jsonable_python

new_messages = result.new_messages()

# 模拟写入 JSONB
message_list = to_jsonable_python(new_messages)
assert isinstance(message_list, list)

# 模拟从 JSONB 读取
restored_messages = ModelMessagesTypeAdapter.validate_python(
    message_list
)

assert restored_messages == new_messages
```

同时验证 `dump_python()` 的返回值不能再次 `model_dump()`：

```python
serialized = ModelMessagesTypeAdapter.dump_python(
    new_messages,
    mode="json",
)
assert isinstance(serialized, list)
```

### 16.2 完整工具链流式事件

对 `run_stream_events()` 验证：

- 工具调用前的过渡 `TextPart` 不会被当作最终回答发送；
- 工具执行后会继续产生最终 `ModelResponse(TextPart)`；
- `AgentRunResultEvent` 是事件流最后的完成结果；
- 最终展示内容等于 `str(event.result.output)`；
- 相同快照不会重复发送给前端；
- 前端使用覆盖而不是字符串追加；
- `event.result.new_messages()` 可以正常序列化和恢复。

### 16.3 两轮上下文

```text
第一轮：我叫张三
第二轮：我叫什么？
```

第二轮必须能够从数据库历史回答“张三”。

### 16.4 取消保存

客户端中断后验证：

- 事件消费立即停止；
- `run_stream_events()` 上下文被正确退出；
- `capture_run_messages()` 能取得当前 Run 的部分消息；
- 持久化前按最新 `run_id` 过滤，不重复保存旧历史；
- 被中断的响应或请求包含 `state="interrupted"`；
- 前端历史能够展示中断 Run。

### 16.5 历史隔离

下一轮 `message_history`：

- 只包含 `is_reusable_run() == True` 的 Run；
- 不包含 interrupted Run；
- 不包含 incomplete Run；
- 仍包含此前完整 Run。

### 16.6 工具链

正常完成的工具 Run 必须完整保存：

```text
ModelResponse(ToolCallPart)
ModelRequest(ToolReturnPart)
ModelResponse(TextPart)
```

额外验证：不能出现历史以 `ModelRequest(ToolReturnPart)` 结束却被 `is_reusable_run()` 接受的情况。

中断工具 Run 原样保存，但不能进入模型上下文。

### 16.7 幂等提交

同一个 `(session_id, run_id)` 重复提交：

- 只产生一条数据库记录；
- 两次调用返回同一个 `chat_message.id`。

### 16.8 摘要

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

> **一条 `chat_message` 保存一次 PydanticAI Run；`chat_message.id` 同时作为持久化顺序和摘要游标；主链路使用 `run_stream_events()` 完整执行工具链；正常 Run 保存最终 `AgentRunResult.new_messages()`，中断 Run 保存 `capture_run_messages()` 捕获的当前 Run 消息；前端展示全部历史，模型上下文和摘要只使用语义完整 Run；摘要后置执行，失败不影响主对话。**

---

## 20. 官方参考

- PydanticAI — Messages and chat history  
  https://pydantic.dev/docs/ai/core-concepts/message-history/

- PydanticAI — Cancelling streams and interrupted history  
  https://pydantic.dev/docs/ai/core-concepts/output/#cancelling-streams

- PydanticAI — Agent streaming events  
  https://pydantic.dev/docs/ai/core-concepts/agent/#streaming-all-events

- PydanticAI — `run_stream_events()` API  
  https://pydantic.dev/docs/ai/api/pydantic-ai/agent/#pydantic_ai.agent.Agent.run_stream_events

- PydanticAI — `capture_run_messages()` API  
  https://pydantic.dev/docs/ai/api/pydantic-ai/agent/#pydantic_ai.agent.capture_run_messages

- PydanticAI — Messages API  
  https://pydantic.dev/docs/ai/api/pydantic-ai/messages/

- Starlette — Request disconnect detection  
  https://www.starlette.io/requests/

- Starlette — Background tasks  
  https://www.starlette.io/background/

---

## 附录 A：最小验证程序

该程序验证五件事：

1. `run_stream_events()` 会完整执行工具链；
2. 工具调用前的过渡文本不会被误当成最终回答；
3. 事件流最终产生 `AgentRunResultEvent`；
4. `result.new_messages()` 可以直接转换为 JSONB 数据；
5. JSONB 数据可以恢复为原生 `ModelMessage`。

```python
"""PydanticAI 完整工具链流式事件与消息序列化验证。"""

from __future__ import annotations

import asyncio
from pprint import pprint

import httpx
from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
    FinalResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPartDelta,
)
from pydantic_ai.messages import ModelMessagesTypeAdapter, TextPart
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_core import to_jsonable_python

from config import AGENT_MODEL, DEEPSEEK_API_KEY


HTTP_PROXY = "http://127.0.0.1:9099"


def create_http_client() -> httpx.AsyncClient:
    if HTTP_PROXY:
        transport = httpx.AsyncHTTPTransport(
            proxy=HTTP_PROXY,
            verify=False,
        )
        return httpx.AsyncClient(
            transport=transport,
            timeout=30.0,
        )

    return httpx.AsyncClient(timeout=30.0)


def build_snapshot(parts: dict[int, str]) -> str:
    return "".join(
        parts[index]
        for index in sorted(parts)
    )


async_http_client = create_http_client()

agent = Agent(
    OpenAIChatModel(
        model_name=AGENT_MODEL,
        provider=DeepSeekProvider(
            api_key=DEEPSEEK_API_KEY,
            http_client=async_http_client,
        ),
    ),
    system_prompt=(
        "请用用户的语言回答问题，回答内容尽量简洁明了，"
        "必要时可以给出示例。"
    ),
)


@agent.tool_plain
async def get_weather(city: str) -> str:
    """查询某个城市的实时天气。"""
    mock = {
        "北京": {"temp": 32, "condition": "晴", "humidity": "45%"},
        "上海": {"temp": 28, "condition": "多云", "humidity": "70%"},
        "广州": {"temp": 35, "condition": "雷阵雨", "humidity": "85%"},
        "深圳": {"temp": 31, "condition": "阴", "humidity": "78%"},
    }
    data = mock.get(
        city,
        {"temp": 25, "condition": "未知", "humidity": "60%"},
    )
    return (
        f"{city} 当前天气：{data['condition']}，"
        f"{data['temp']}°C，湿度 {data['humidity']}"
    )


async def main() -> None:
    final_result = None
    final_output_started = False
    text_parts: dict[int, str] = {}
    previous_snapshot: str | None = None

    try:
        async with agent.run_stream_events(
            "你好啊，我是沣，帮我查查北京的天气"
        ) as events:
            async for event in events:
                print(type(event).__name__, event)

                if isinstance(event, PartStartEvent):
                    if event.index == 0:
                        text_parts.clear()
                        final_output_started = False

                    if isinstance(event.part, TextPart):
                        text_parts[event.index] = event.part.content

                elif isinstance(event, FinalResultEvent):
                    final_output_started = True
                    snapshot = build_snapshot(text_parts)

                    if snapshot and snapshot != previous_snapshot:
                        previous_snapshot = snapshot
                        print("snapshot:", snapshot)

                elif (
                    final_output_started
                    and isinstance(event, PartDeltaEvent)
                    and isinstance(event.delta, TextPartDelta)
                ):
                    text_parts[event.index] = (
                        text_parts.get(event.index, "")
                        + event.delta.content_delta
                    )
                    snapshot = build_snapshot(text_parts)

                    if snapshot != previous_snapshot:
                        previous_snapshot = snapshot
                        print("snapshot:", snapshot)

                elif isinstance(event, AgentRunResultEvent):
                    final_result = event.result

        if final_result is None:
            raise RuntimeError("Agent 未产生最终结果")

        print("\n=== final output ===")
        print(final_result.output)

        new_messages = final_result.new_messages()

        print("\n=== new_messages_json ===")
        print(final_result.new_messages_json().decode("utf-8"))

        print("\n=== native ModelMessage objects ===")
        pprint(new_messages)

        message_list = to_jsonable_python(new_messages)

        print("\n=== JSONB message_list ===")
        pprint(message_list, sort_dicts=False)

        restored_messages = (
            ModelMessagesTypeAdapter.validate_python(
                message_list
            )
        )

        assert restored_messages == new_messages

        # 工具 Run 的最终一条生命周期消息必须是最终 ModelResponse。
        assert new_messages[-1].kind == "response"

        print("\n完整工具链与 JSONB Round-trip 验证通过。")

    finally:
        await async_http_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
```
