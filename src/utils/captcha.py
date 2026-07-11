"""
验证码生成与校验。

支持 Redis（优先）和内存 fallback 两种存储。
"""

from __future__ import annotations

import random
import string
import time
import uuid
from typing import Any

from log import logger
from utils.cache import cache_manager

CAPTCHA_TTL = 300  # 5 分钟
CAPTCHA_PREFIX = "captcha"

# ── 内存 fallback 存储 ─────────────────────────────────────────────

_memory_store: dict[str, dict[str, Any]] = {}


def _clean_expired_memory() -> None:
    now = time.time()
    expired = [k for k, v in _memory_store.items() if v.get("expires", 0) < now]
    for k in expired:
        _memory_store.pop(k, None)


def _store_in_memory(captcha_id: str, text: str) -> None:
    _memory_store[captcha_id] = {
        "text": text,
        "expires": time.time() + CAPTCHA_TTL,
    }


def _verify_from_memory(captcha_id: str, code: str) -> bool:
    record = _memory_store.get(captcha_id)
    if not record:
        return False
    if time.time() > record.get("expires", 0):
        _memory_store.pop(captcha_id, None)
        return False
    valid = record["text"].lower() == code.lower().strip()
    _memory_store.pop(captcha_id, None)  # 一次性校验
    return valid


# ── SVG 生成 ───────────────────────────────────────────────────────

_CHARS = string.digits
_SVG_WIDTH = 130
_SVG_HEIGHT = 44
_FONT_SIZE = 28


def _rand_color(min_val: int = 0, max_val: int = 180) -> str:
    r = random.randint(min_val, max_val)
    g = random.randint(min_val, max_val)
    b = random.randint(min_val, max_val)
    return f"rgb({r},{g},{b})"


def generate_captcha_text(length: int = 4) -> str:
    """生成随机数字验证码文本。"""
    return "".join(random.choices(_CHARS, k=length))


def _char_svg(char: str, x: float, y: float, angle: float, color: str) -> str:
    """单个字符的 SVG text 元素（带旋转）。"""
    transform = f"rotate({angle:.1f} {x:.1f} {y:.1f})"
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" '
        f'transform="{transform}" '
        f'font-size="{_FONT_SIZE}" '
        f'font-family="Arial, sans-serif" '
        f'font-weight="bold" '
        f'fill="{color}" '
        f'text-anchor="middle" '
        f'dominant-baseline="central">'
        f"{char}</text>"
    )


def generate_captcha_svg(text: str) -> str:
    """生成带噪点/噪线的 SVG 验证码图片。"""
    cx = _SVG_WIDTH / 2
    cy = _SVG_HEIGHT / 2
    spacing = 22
    start_x = cx - ((len(text) - 1) * spacing) / 2

    lines: list[str] = []
    # 背景
    bg_color = _rand_color(230, 255)
    lines.append(
        f'<rect width="{_SVG_WIDTH}" height="{_SVG_HEIGHT}" '
        f'fill="{bg_color}" rx="6" />'
    )

    # 噪线
    for _ in range(random.randint(3, 5)):
        x1 = random.randint(0, _SVG_WIDTH)
        y1 = random.randint(0, _SVG_HEIGHT)
        x2 = random.randint(0, _SVG_WIDTH)
        y2 = random.randint(0, _SVG_HEIGHT)
        color = _rand_color(100, 200)
        lines.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="{color}" stroke-width="{random.uniform(0.5, 1.5):.1f}" '
            f'stroke-linecap="round" />'
        )

    # 噪点
    for _ in range(random.randint(30, 60)):
        px = random.randint(0, _SVG_WIDTH)
        py = random.randint(0, _SVG_HEIGHT)
        r = random.randint(1, 2)
        color = _rand_color(120, 200)
        lines.append(
            f'<circle cx="{px}" cy="{py}" r="{r}" '
            f'fill="{color}" opacity="0.6" />'
        )

    # 字符
    for i, ch in enumerate(text):
        x = start_x + i * spacing
        y = cy + random.uniform(-4, 4)
        angle = random.uniform(-25, 25)
        color = _rand_color(20, 120)
        lines.append(_char_svg(ch, x, y, angle, color))

    # 额外随机弧线
    if random.random() > 0.5:
        x1 = random.randint(0, _SVG_WIDTH // 2)
        y1 = random.randint(0, _SVG_HEIGHT)
        x2 = random.randint(_SVG_WIDTH // 2, _SVG_WIDTH)
        y2 = random.randint(0, _SVG_HEIGHT)
        color = _rand_color(140, 200)
        lines.append(
            f'<path d="M{x1} {y1} Q{_SVG_WIDTH / 2} {random.randint(0, _SVG_HEIGHT)} {x2} {y2}" '
            f'stroke="{color}" stroke-width="1" fill="none" />'
        )

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{_SVG_WIDTH}" height="{_SVG_HEIGHT}" '
        f'viewBox="0 0 {_SVG_WIDTH} {_SVG_HEIGHT}">'
        f'{"".join(lines)}'
        f"</svg>"
    )
    return svg


def generate_captcha_id() -> str:
    """生成唯一验证码 ID。"""
    return uuid.uuid4().hex[:16]


async def store_captcha(captcha_id: str, text: str) -> None:
    """存储验证码（Redis 优先，内存 fallback）。"""
    key = f"{CAPTCHA_PREFIX}:{captcha_id}"
    ok = await cache_manager.set(key, text, ttl=CAPTCHA_TTL)
    if not ok:
        _store_in_memory(captcha_id, text)


async def verify_captcha(captcha_id: str, code: str) -> bool:
    """校验验证码（消费后即删除）。"""
    if not captcha_id or not code:
        return False

    key = f"{CAPTCHA_PREFIX}:{captcha_id}"
    stored = await cache_manager.get(key)
    if stored is not None:
        await cache_manager.delete(key)  # 一次性
        return stored.lower() == code.lower().strip()

    # fallback 内存
    return _verify_from_memory(captcha_id, code)


def cleanup_expired() -> None:
    _clean_expired_memory()
