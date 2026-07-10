"""Agent HTTP routes. Uses pydantic-ai's AGUIAdapter for SSE formatting."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from core.dependency import CurrentUser
from db.session import get_db
from schemas import ChatRequest, ChatResponse, Success
from services import AgentService, ConversationService

router = APIRouter()


@router.post("/chat/completions", summary="AI agent chat completion")
async def chat_completion(
    payload: ChatRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),  # type: ignore[valid-type]
):
    result = await AgentService(db).run(payload, current_user.id)
    return Success(data=ChatResponse(**result).model_dump())


@router.post("/chat/stream", summary="Agent SSE streaming chat")
async def chat_stream(
    request: Request,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),  # type: ignore[valid-type]
):
    service = AgentService(db)
    return await service.stream_ag_ui(request, current_user.id)


@router.post(
    "/chat/messages/{message_id}/retry", summary="Retry a failed message as SSE"
)
async def retry_chat_message(
    message_id: int,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),  # type: ignore[valid-type]
):
    return await AgentService(db).retry_stream(message_id, current_user.id)


@router.post("/create_conversation", summary="Create conversation")
async def create_conversation(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),  # type: ignore[valid-type]
):
    result = await ConversationService(db).create(user_id=current_user.id)
    return Success(data=result)


@router.get("/all_conversation_list", summary="List conversations")
async def all_conversation_list(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),  # type: ignore[valid-type]
):
    data = await ConversationService(db).list_by_user(user_id=current_user.id)
    return Success(data=data)


@router.get("/get_conversation", summary="Get conversation messages")
async def get_conversation(
    session_id: str,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),  # type: ignore[valid-type]
):
    data = await ConversationService(db).get_messages(
        session_id=session_id,
        user_id=current_user.id,
    )
    return Success(data=data)
