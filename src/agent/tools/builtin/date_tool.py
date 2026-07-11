"""Built-in date/time tools.

No parameters — pydantic-ai uses the function signature as the JSON Schema.
"""

from datetime import datetime, timezone, timedelta


async def current_time() -> str:
    """Return the current date in Asia/Shanghai (UTC+8)."""
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz)
    weekdays = ['一', '二', '三', '四', '五', '六', '日']
    return f"{now.strftime('%Y年%-m月%-d日')} 星期{weekdays[now.weekday()]}"
