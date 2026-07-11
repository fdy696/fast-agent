"""Background tasks — SAQ Worker."""


async def send_feishu_alert(ctx, *, title, content):
    from utils.feishu import send_alert
    await send_alert(title, content)


FUNCTIONS = [
    send_feishu_alert,
]
