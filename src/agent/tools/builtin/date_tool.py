"""Built-in date/time tools.

No parameters — pydantic-ai uses the function signature as the JSON Schema.
"""

from datetime import datetime, timezone, timedelta


async def current_time() -> str:
    """Return the current time in Asia/Shanghai (UTC+8) as an ISO 8601 string."""
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).isoformat()
