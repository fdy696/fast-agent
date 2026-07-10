"""Background tasks — SAQ Worker."""
from db.session import AsyncSessionLocal


async def generate_title(ctx, *, session_id, first_msg):
    from repositories.chat_session import ChatSessionRepository
    from agent.prompts import generate_title as do_generate_title

    title = await do_generate_title(first_msg)
    async with AsyncSessionLocal() as db:
        session = await ChatSessionRepository(db).get_by_session_id(
            session_id=session_id, user_id=0
        )
        if session:
            await ChatSessionRepository(db).update_title(session, title)


async def send_feishu_alert(ctx, *, title, content):
    from utils.feishu import send_alert
    await send_alert(title, content)


FUNCTIONS = [
    generate_title,
    send_feishu_alert,
]
