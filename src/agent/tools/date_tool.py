"""get_current_date — 返回当前系统日期"""

from datetime import datetime, timezone
from typing import TypedDict


class DateResult(TypedDict):
    current_date: str
    weekday: str


async def get_current_date() -> DateResult:
    now = datetime.now(timezone.utc)
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return {
        "current_date": now.strftime("%Y-%m-%d"),
        "weekday": weekdays[now.weekday()],
    }
