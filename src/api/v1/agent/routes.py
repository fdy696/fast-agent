"""Chat sessions and PydanticAI-native AG-UI streaming."""

import uuid

from ag_ui.core import RunAgentInput
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic_ai.ui import SSE_CONTENT_TYPE
from pydantic_ai.ui.ag_ui import AGUIEventStream
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from core.dependency import CurrentUser
from db.session import get_db
from schemas.chat import ChatRequest
from services.chat_service import ChatService
from services.summary_service import SummaryService

router = APIRouter()


@router.post("/sessions", summary="Create chat session")
async def create_session(
    current_user: CurrentUser, db: AsyncSession = Depends(get_db)
):
    return await ChatService(db).create_session(current_user.id)


@router.get("/sessions", summary="List chat sessions")
async def list_sessions(
    current_user: CurrentUser, db: AsyncSession = Depends(get_db)
):
    return await ChatService(db).list_sessions(current_user.id)


@router.post("/sessions/{session_id}/archive", summary="Archive chat session")
async def archive_session(
    session_id: str,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    updated = await ChatService(db).set_session_status(
        session_id=session_id, user_id=current_user.id, status="archived"
    )
    if not updated:
        raise HTTPException(404, "Session not found")
    return {"status": "ok"}


@router.post("/sessions/{session_id}/delete", summary="Delete chat session")
async def delete_session(
    session_id: str,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    updated = await ChatService(db).set_session_status(
        session_id=session_id, user_id=current_user.id, status="deleted"
    )
    if not updated:
        raise HTTPException(404, "Session not found")
    return {"status": "ok"}


@router.get("/sessions/{session_id}/messages", summary="Get persisted messages")
async def get_messages(
    session_id: str,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
):
    try:
        return await ChatService(db).list_messages(
            session_id=session_id, user_id=current_user.id
        )
    except LookupError as exc:
        raise HTTPException(404, "Session not found") from exc


@router.post("/sessions/{session_id}/messages", summary="Stream agent run")
async def send_message(
    request: Request,
    session_id: str,
    body: ChatRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    service = ChatService(db)
    session = await service.repository.get_session(
        session_id=session_id, user_id=current_user.id
    )
    if session is None:
        raise HTTPException(404, "Session not found")

    native_events = service.stream_native_events(
        request=request,
        session_id=session_id,
        user_id=current_user.id,
        prompt=body.content,
    )
    accept = request.headers.get("accept", SSE_CONTENT_TYPE)
    run_input = RunAgentInput(
        thread_id=session_id,
        run_id=str(uuid.uuid4()),
        messages=[],
        tools=[],
        context=[],
        forwarded_props={},
        state={},
    )
    event_stream = AGUIEventStream(run_input=run_input, accept=accept)
    protocol_events = event_stream.transform_stream(native_events)
    return StreamingResponse(
        event_stream.encode_stream(protocol_events),
        media_type=event_stream.content_type,
        headers=event_stream.response_headers,
        background=BackgroundTask(SummaryService().try_summarize, session_id),
    )
