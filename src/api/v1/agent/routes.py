"""Agent HTTP routes. Keep this layer thin: HTTP/SSE only."""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.dependency import CurrentUser
from db.session import get_db
from schemas import ChatRequest, ChatResponse, Success
from services import AgentService, ConversationService

router = APIRouter()


def _build_sse_response(
    payload: ChatRequest,
    user_id: int,
    service: AgentService,
) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        try:
            async for event in service.stream(payload, user_id):
                if event.type == "llm.delta":
                    text = str(event.data.get("text", ""))
                    payload_json = {
                        "role": "assistant",
                        "content": text,
                        "event": "text_chunk",
                        "data": {"text": text},
                    }
                elif event.type == "tool.call":
                    payload_json = {
                        "role": "tool",
                        "content": event.data,
                        "event": "tool_call",
                        "data": event.data,
                    }
                elif event.type == "tool.result":
                    payload_json = {
                        "role": "tool",
                        "content": event.data,
                        "event": "tool_result",
                        "data": event.data,
                    }
                elif event.type == "error":
                    payload_json = {
                        "role": "error",
                        "content": event.data.get("message", "Agent runtime error"),
                        "event": "error",
                        "data": event.data,
                    }
                    yield f"data: {json.dumps(payload_json, ensure_ascii=False)}\n\n"
                    return
                elif event.type == "done":
                    payload_json = {"role": "done", "content": "finish", "event": "done", "data": {}}
                else:
                    continue

                yield f"data: {json.dumps(payload_json, ensure_ascii=False)}\n\n"
        except Exception as exc:
            payload_json = {
                "role": "error",
                "content": str(exc),
                "event": "error",
                "data": {"message": str(exc)},
            }
            yield f"data: {json.dumps(payload_json, ensure_ascii=False)}\n\n"


    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat/completions", summary="AI agent chat completion")
async def chat_completion(
    payload: ChatRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    result = await AgentService(db).run(payload, current_user.id)
    return Success(data=ChatResponse(**result).model_dump())


@router.post("/chat/stream", summary="Agent SSE streaming chat")
async def chat_stream(
    payload: ChatRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    service = AgentService(db)
    return _build_sse_response(payload, current_user.id, service)


@router.get("/create_conversation", summary="Create conversation")
async def create_conversation(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    result = await ConversationService(db).create(user_id=current_user.id)
    return Success(data=result)


@router.get("/all_conversation_list", summary="List conversations")
async def all_conversation_list(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    data = await ConversationService(db).list_by_user(user_id=current_user.id)
    return Success(data=data)


@router.post("/get_conversation", summary="Get conversation messages")
async def get_conversation(
    payload: ChatRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    data = await ConversationService(db).get_messages(
        session_id=payload.session_id or "",
        user_id=current_user.id,
    )
    return Success(data=data)
