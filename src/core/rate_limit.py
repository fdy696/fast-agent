from slowapi import Limiter
from slowapi.util import get_remote_address

from core.config import settings

_default_limits = [
    item.strip()
    for item in settings.RATE_LIMIT_DEFAULT.split(",")
    if item.strip()
]

limiter_kwargs = {
    "key_func": get_remote_address,
    "default_limits": _default_limits,
    "in_memory_fallback_enabled": True,
}

if settings.RATE_LIMIT_STORAGE_URL:
    limiter_kwargs["storage_uri"] = settings.RATE_LIMIT_STORAGE_URL

limiter = Limiter(**limiter_kwargs)
