"""s04‑style hook 系统 — 挂在循环上，不写进循环里"""

from typing import Any, Callable

HOOKS: dict[str, list[Callable[..., Any]]] = {}


def register_hook(event: str, callback: Callable[..., Any]) -> None:
    HOOKS.setdefault(event, []).append(callback)


def trigger_hooks(event: str, *args: Any) -> Any:
    """按序触发；任一回调返回非 None 则立即返回该值。"""
    for callback in HOOKS.get(event, []):
        result = callback(*args)
        if result is not None:
            return result
    return None
