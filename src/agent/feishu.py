"""飞书 Webhook 告警推送"""
import json
import httpx
from core.config import settings
from log import logger


async def send_alert(title: str, content: str) -> bool:
    if not settings.FEISHU_WEBHOOK_URL:
        return False
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(settings.FEISHU_WEBHOOK_URL, json={
                "msg_type": "interactive",
                "card": {
                    "header": {"title": {"tag": "plain_text", "content": title}, "template": "red"},
                    "elements": [{"tag": "markdown", "content": content}],
                },
            }, timeout=10)
            return r.status_code == 200
    except Exception as e:
        logger.error(f"飞书推送失败: {e}")
        return False
