import asyncio
import json
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from agent.agent_loop import main_model
from agent.context_store import get_user_chat_list, get_chat_detail, get_travel_plan
from core.dependency import CurrentUser, DbSession
from core.rate_limit import limiter
from schemas.agent import ChatRequest, GetConversationRequest, GetTravelPlanRequest
from schemas.base import Fail, Success

router = APIRouter(tags=["AI Agent"])


@router.post("/chat", summary="SSE 流式聊天")
@limiter.limit("30/minute")
async def chat(
    request: Request,
    req: ChatRequest,
    current_user: CurrentUser,
):
    # 不注入 DbSession —— main_model 内部开短 session，避免流式期间整段持有 PG 连接
    queue: asyncio.Queue = asyncio.Queue()

    async def event_generator():
        completed = False

        # 启动 agent 循环（后台任务）
        task = asyncio.create_task(
            main_model(
                session_id=req.sessionId,
                user_id=current_user.id,
                content=req.content,
                queue=queue,
            )
        )

        try:
            while True:
                msg = await queue.get()
                if msg is None:  # sentinel — agent 正常完成
                    completed = True
                    yield f"data: {json.dumps({'role': 'done', 'content': 'finish'}, ensure_ascii=False)}\n\n"
                    return
                msg_type, payload = msg
                yield f"data: {json.dumps({'role': msg_type, 'content': payload}, ensure_ascii=False)}\n\n"
        finally:
            # 正常完成时不 cancel，只回收异常；客户端断开时才 cancel 避免白扣 token
            if not completed and not task.done():
                task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/create_conversation", summary="创建新会话")
async def create_conversation(current_user: CurrentUser):
    session_id = str(uuid.uuid4())
    return Success(data={"session_id": session_id})


@router.get("/all_conversation_list", summary="当前用户全部会话")
async def all_conversation_list(
    current_user: CurrentUser,
    session: DbSession,
):
    res = await get_user_chat_list(current_user.id, session)
    return Success(data=res)


@router.post("/get_conversation", summary="获取会话历史")
async def get_conversation(
    req: GetConversationRequest,
    current_user: CurrentUser,
    session: DbSession,
):
    res = await get_chat_detail(current_user.id, req.session_id, session)
    return Success(data=res)


@router.post("/get_travel_plan", summary="获取行程计划")
async def travel_plan(
    req: GetTravelPlanRequest,
    current_user: CurrentUser,
    session: DbSession,
):
    res = await get_travel_plan(req.plan_id, req.session_id, current_user.id, session)
    if res is None:
        return Fail(code=404, msg="未找到该行程计划")
    return Success(data=res)
