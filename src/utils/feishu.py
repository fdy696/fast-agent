"""飞书 Webhook 告警 — fire-and-forget."""

import httpx
from core.config import settings
from log import logger

FEISHU_TIMEOUT = 5

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=FEISHU_TIMEOUT)
    return _client


async def send_alert(title: str, content: str) -> None:
    """发送飞书机器人消息。调用方负责 create_task。"""
    url = settings.FEISHU_WEBHOOK_URL
    if not url:
        return
    try:
        await _get_client().post(url, json={
            "msg_type": "interactive",
            "card": {
                "header": {"title": {"content": title, "tag": "plain_text"}},
                "elements": [{"tag": "markdown", "content": content}],
            },
        })
    except Exception as e:
        logger.warning(f"飞书告警发送失败: {e}")


async def close_feishu() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
