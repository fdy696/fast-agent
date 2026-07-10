"""Agent HTTP routes — session CRUD + message lifecycle."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from core.dependency import CurrentUser
from db.session import get_db
from repositories.chat_session import ChatSessionRepository
from services.chat_service import ChatService

router = APIRouter()

@router.post("/sessions", summary="Create session")
async def create_session(current_user: CurrentUser, db: AsyncSession = Depends(get_db)):
    repo = ChatSessionRepository(db)
    sid = str(uuid.uuid4())
    session = await repo.create(session_id=sid, user_id=current_user.id)
    return {"id": session.id, "session_id": session.session_id, "title": session.title, "created_at": session.created_at.isoformat() if session.created_at else None}

@router.get("/sessions", summary="List sessions")
async def list_sessions(current_user: CurrentUser, db: AsyncSession = Depends(get_db)):
    repo = ChatSessionRepository(db)
    _, sessions = await repo.list_by_user(user_id=current_user.id)
    return [{"id": s.id, "session_id": s.session_id, "title": s.title, "status": s.status, "updated_at": s.updated_at.isoformat() if s.updated_at else None} for s in sessions]

@router.post("/sessions/{session_id}/archive", summary="Archive session")
async def archive_session(session_id: str, current_user: CurrentUser, db: AsyncSession = Depends(get_db)):
    repo = ChatSessionRepository(db)
    session = await repo.get_by_session_id(session_id=session_id, user_id=current_user.id)
    if not session: raise HTTPException(404, "Session not found")
    await repo.archive(session)
    return {"status": "ok"}

@router.post("/sessions/{session_id}/delete", summary="Soft delete session")
async def delete_session(session_id: str, current_user: CurrentUser, db: AsyncSession = Depends(get_db)):
    repo = ChatSessionRepository(db)
    session = await repo.get_by_session_id(session_id=session_id, user_id=current_user.id)
    if not session: raise HTTPException(404, "Session not found")
    await repo.soft_delete(session)
    return {"status": "ok"}

@router.post("/messages", summary="Create pending message")
async def create_message(body: dict, current_user: CurrentUser, db: AsyncSession = Depends(get_db)):
    session_id = body.get("session_id")
    content = body.get("content", "")
    if not session_id or not content.strip(): raise HTTPException(400, "session_id and content are required")
    service = ChatService(db)
    return await service.create_pending_turn(session_id=session_id, user_id=current_user.id, content=content.strip())

@router.post("/messages/{message_id}/stream", summary="Stream execute message")
async def stream_message(message_id: int, current_user: CurrentUser, db: AsyncSession = Depends(get_db)):
    service = ChatService(db)
    return await service.execute_turn(message_id, current_user.id)

@router.post("/messages/{message_id}/retry", summary="Retry failed message")
async def retry_message(message_id: int, current_user: CurrentUser, db: AsyncSession = Depends(get_db)):
    service = ChatService(db)
    return await service.retry_turn(message_id, current_user.id)

@router.get("/messages", summary="Get session messages")
async def get_messages(session_id: str = Query(...), after_id: int | None = Query(None), current_user: CurrentUser = Depends(), db: AsyncSession = Depends(get_db)):
    service = ChatService(db)
    return await service.list_messages(session_id=session_id, user_id=current_user.id, after_id=after_id)
