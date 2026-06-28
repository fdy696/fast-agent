"""Token 费用计算 + 每日累计告警"""

from datetime import datetime, timezone, timedelta

_CST = timezone(timedelta(hours=8))

# 每百万 token 美元定价
PRICING = {
    "deepseek-v4-pro":   {"input": 2.0, "cached_input": 0.145, "output": 8.0},
    "deepseek-v4-flash": {"input": 0.27, "cached_input": 0.014, "output": 1.1},
}

_daily_cost: float = 0.0
_daily_date: str | None = None


def calculate(usage: dict, model: str = "deepseek-v4-pro") -> float:
    """单次 LLM 调用的费用（美元）。usage 包含 prompt_tokens / completion_tokens / cached_tokens。"""
    p = PRICING.get(model, PRICING["deepseek-v4-pro"])
    cached = usage.get("cached_tokens", 0)
    uncached = usage.get("prompt_tokens", 0) - cached
    completion = usage.get("completion_tokens", 0)
    return (uncached * p["input"] + cached * p["cached_input"] + completion * p["output"]) / 1_000_000


def track_daily(cost: float, limit: float) -> bool:
    """累加每日费用，返回 True 表示超限。"""
    global _daily_cost, _daily_date
    today = datetime.now(_CST).strftime("%Y%m%d")
    if _daily_date != today:
        _daily_cost = 0.0
        _daily_date = today
    _daily_cost += cost
    return _daily_cost > limit


def daily_summary() -> dict:
    """返回当日费用摘要。"""
    return {
        "date": _daily_date or datetime.now(_CST).strftime("%Y%m%d"),
        "cost": round(_daily_cost, 6),
    }
