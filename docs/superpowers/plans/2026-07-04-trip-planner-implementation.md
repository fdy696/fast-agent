# 旅行规划地图增量渲染 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在通用 Agent 聊天中，当用户请求旅行规划时，通过三阶段编排 + MCP 工具拦截 + SSE 自定义事件，实现行程地图的增量流式渲染。

**Architecture:** 纯函数 Planner 编排同一个通用 Agent 跑 3 次（数据收集 → 路线规划 → 行程流式生成）。`Hooks + contextvars` 拦截 MCP 工具调用结果提取坐标，`run_event_stream` hook 将 `MAP_DATA` 事件注入 SSE 流。前端 `TravelMap.vue` 增量更新地图标记和路线。

**Tech Stack:** Python 3.13, pydantic-ai 2.0, FastAPI, Vue 3 + TypeScript, 高德 MCP (dashscope), AMap JS API 2.0

## Global Constraints

- 主 Agent (`model_client.py`) 只能加一行 `capabilities=[trip_hooks]`，不修改 Agent 内部逻辑
- `agent_service.py` 的通用对话路径不变，只在开头加意图检测 → planner 路由
- 前端的 `useChatStream.ts` 保持现有接口兼容，新增 `onMapData` / `onProgress` 可选参数
- 所有 MCP 工具结果解析加 try/except 保护，解析失败不阻断主流程

---

### Task 1: 事件模型 `events.py`

**Files:**
- Create: `src/agent/trip/__init__.py`
- Create: `src/agent/trip/events.py`

**Interfaces:**
- Produces: `ProgressEvent(phase, progress, message)`, `MapEvent(type, markers, polyline, forecasts)`, `DoneEvent(progress)` — 每个类有 `to_sse()` 方法返回 `str`

- [ ] **Step 1: 创建模块目录和 `__init__.py`**

```bash
mkdir -p src/agent/trip
```

```python
# src/agent/trip/__init__.py
"""旅行规划模块 — Trip planner hooks, planner, extractors."""
```

- [ ] **Step 2: 创建 `events.py`**

```python
# src/agent/trip/events.py
"""SSE 自定义事件模型。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class ProgressEvent:
    """阶段进度事件 — planner yield 到 SSE 流。"""
    phase: str       # 'data_collection' | 'route_planning' | 'generating'
    progress: float  # 0.0 – 1.0
    message: str     # 用户可见的进度文案

    def to_sse(self) -> str:
        payload = {
            "type": "PROGRESS",
            "phase": self.phase,
            "progress": self.progress,
            "message": self.message,
        }
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@dataclass
class MapEvent:
    """地图数据事件 — hooks 拦截 MCP 工具结果后产生。"""
    type: str  # 'POI_FOUND' | 'ROUTE_SEGMENT' | 'WEATHER_UPDATE'
    markers: list[dict] = field(default_factory=list)
    polyline: list[dict] = field(default_factory=list)
    forecasts: list[dict] = field(default_factory=list)

    def to_sse(self) -> str:
        payload = {
            "type": "MAP_DATA",
            "payload": {
                "type": self.type,
                "markers": self.markers,
                "polyline": self.polyline,
                "forecasts": self.forecasts,
            },
        }
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@dataclass
class DoneEvent:
    """行程规划完成事件。"""
    progress: float = 1.0

    def to_sse(self) -> str:
        return f"data: {json.dumps({'type': 'TRIP_DONE', 'progress': self.progress}, ensure_ascii=False)}\n\n"
```

- [ ] **Step 3: 验证导入**

```bash
cd src && python3 -c "from agent.trip.events import ProgressEvent, MapEvent, DoneEvent; e = ProgressEvent('test', 0.5, 'msg'); print(e.to_sse())"
```

- [ ] **Step 4: Commit**

```bash
git add src/agent/trip/__init__.py src/agent/trip/events.py
git commit -m "feat(trip): add event models (ProgressEvent, MapEvent, DoneEvent)"
```

---

### Task 2: MCP 结果提取器 `extractors.py`

**Files:**
- Create: `src/agent/trip/extractors.py`

**Interfaces:**
- Produces: `extract_pois(result: Any) -> list[PoiInfo]`, `extract_route(result: Any) -> list[list[float]] | None`, `extract_weather(result: Any) -> list[dict] | None`

- [ ] **Step 1: 创建 `extractors.py`**

```python
# src/agent/trip/extractors.py
"""高德 MCP 工具返回值解析。每个函数独立处理一种工具返回格式。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass
class PoiInfo:
    name: str
    lng: float
    lat: float
    address: str = ""


def _parse_json(result: Any) -> dict | None:
    """将可能的 JSON 字符串解析为 dict。"""
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        try:
            return json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def extract_pois(result: Any) -> list[PoiInfo]:
    """从 maps_text_search / maps_around_search / maps_search_detail 结果提取 POI。"""
    data = _parse_json(result)
    if not data:
        return []

    pois = data.get("pois") or data.get("results") or []
    items: list[PoiInfo] = []
    for p in pois:
        loc = p.get("location", "")
        if not loc or "," not in str(loc):
            continue
        try:
            lng_str, lat_str = str(loc).split(",", 1)
            lng, lat = float(lng_str), float(lat_str)
        except (ValueError, IndexError):
            continue
        items.append(PoiInfo(
            name=p.get("name", ""),
            lng=lng,
            lat=lat,
            address=p.get("address", ""),
        ))
    return items


def extract_route(result: Any) -> list[list[float]] | None:
    """从 maps_direction_* 结果提取第一条路线的 polyline。"""
    data = _parse_json(result)
    if not data:
        return None

    routes = data.get("routes") or []
    if not routes:
        return None

    # 不同方向工具返回格式可能有 path / polyline 字段
    first = routes[0]
    path = first.get("path") or first.get("polyline") or ""
    if not path:
        return None

    points: list[list[float]] = []
    for pair in str(path).split(";"):
        if "," in pair:
            try:
                l, a = pair.split(",", 1)
                points.append([float(l), float(a)])
            except (ValueError, IndexError):
                continue
    return points if points else None


def extract_weather(result: Any) -> list[dict] | None:
    """从 maps_weather 结果提取天气预报。"""
    data = _parse_json(result)
    if not data:
        return None

    forecasts = data.get("forecasts") or []
    if not forecasts:
        return None

    return [
        {
            "date": f.get("date", ""),
            "day_weather": f.get("dayweather", ""),
            "day_temp": f.get("daytemp", ""),
            "night_temp": f.get("nighttemp", ""),
        }
        for f in forecasts
    ]
```

- [ ] **Step 2: 验证模块可导入**

```bash
cd src && python3 -c "from agent.trip.extractors import extract_pois, extract_route, extract_weather; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add src/agent/trip/extractors.py
git commit -m "feat(trip): add MCP result extractors (poi, route, weather)"
```

---

### Task 3: 结构化 Schema `schemas.py`

**Files:**
- Create: `src/agent/trip/schemas.py`

**Interfaces:**
- Produces: `TripData(pois: list[PoiItem], weather: list[WeatherItem])`, `RouteData(routes: list[RouteSegment])` — Phase 1/2 的 `result_type`

- [ ] **Step 1: 创建 `schemas.py`**

```python
# src/agent/trip/schemas.py
"""Phase 1/2 结构化输出 schema — pydantic-ai result_type。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class PoiItem(BaseModel):
    name: str
    lng: float
    lat: float
    address: str = ""
    category: str = ""


class WeatherItem(BaseModel):
    date: str
    day_weather: str = Field(alias="day_weather")
    day_temp: str = ""
    night_temp: str = ""

    class Config:
        populate_by_name = True


class TripData(BaseModel):
    """Phase 1 输出 — Agent 从 MCP 结果中提取的结构化数据。"""
    pois: list[PoiItem] = Field(min_length=1)
    weather: list[WeatherItem] = Field(default_factory=list)


class RouteSegment(BaseModel):
    from_poi: str
    to_poi: str
    transport_mode: str  # walking | transit | driving | bicycling
    polyline: list[list[float]] = Field(default_factory=list)


class RouteData(BaseModel):
    """Phase 2 输出 — 景点间路线。"""
    routes: list[RouteSegment] = Field(default_factory=list)
```

- [ ] **Step 2: 验证 schema 序列化**

```bash
cd src && python3 -c "
from agent.trip.schemas import TripData, PoiItem, WeatherItem
d = TripData(pois=[PoiItem(name='故宫', lng=116.397, lat=39.916)])
print(d.model_dump_json())
"
```

- [ ] **Step 3: Commit**

```bash
git add src/agent/trip/schemas.py
git commit -m "feat(trip): add structured output schemas (TripData, RouteData)"
```

---

### Task 4: 阶段指令 `prompts.py`

**Files:**
- Create: `src/agent/trip/prompts.py`

**Interfaces:**
- Produces: `PHASE1_PROMPT`, `PHASE2_PROMPT`, `PHASE3_PROMPT` — 三个阶段的 `instructions` 字符串模板，使用 `str.format()` 填充

- [ ] **Step 1: 创建 `prompts.py`**

```python
# src/agent/trip/prompts.py
"""三阶段 Agent 指令模板 — 短、聚焦、单一任务。"""

PHASE1_PROMPT = """你是一个数据收集助手。唯一任务：调用工具获取数据，然后返回 JSON。

## 任务
1. 调用 maps_text_search(keywords="{preferences}", city="{city}") 搜索 6-8 个热门景点
2. 调用 maps_weather(city="{city}") 查询天气

## 额外要求
{free_text}

## 输出格式
只返回 JSON，不要任何解释或建议。JSON 格式：
```json
{{
  "pois": [
    {{"name": "景点名", "lng": 116.397, "lat": 39.916, "address": "地址", "category": "类别"}}
  ],
  "weather": [
    {{"date": "YYYY-MM-DD", "day_weather": "晴", "day_temp": "25", "night_temp": "15"}}
  ]
}}
```
POI 的 lng/lat 必须是数字，来自工具返回结果，不得编造。
搜索偏好: {preferences}"""


PHASE2_PROMPT = """你是一个路线规划助手。对景点列表中每对相邻景点，规划交通路线。

## 景点列表
{pois}

## 城市
{city}

## 任务
对列表中每对相邻景点 (pois[0]→pois[1], pois[1]→pois[2], ...) 调用工具：
- 距离 < 2km: maps_direction_walking(origin="lng,lat", destination="lng,lat")
- 距离 >= 2km: maps_direction_transit_integrated(origin="lng,lat", destination="lng,lat", city="{city}", cityd="{city}")

## 输出格式
只返回 JSON，不要任何解释。JSON 格式：
```json
{{
  "routes": [
    {{
      "from_poi": "景点A",
      "to_poi": "景点B",
      "transport_mode": "walking",
      "polyline": [[116.397, 39.916], [116.398, 39.915]]
    }}
  ]
}}
```
polyline 必须是坐标数组，来自工具返回结果。"""


PHASE3_PROMPT = """你是专业旅行规划师。根据以下数据生成 {city}{days} 日游行程。

## 景点数据
{pois}

## 路线数据
{routes}

## 天气数据
{weather}

## 输出模板
严格遵循此结构输出行程：
{template}

## 额外要求
{free_text}

## 可用工具
- maps_around_search(keywords="餐饮", location="lng,lat", radius=1000) — 搜索景点周边餐饮
- maps_around_search(keywords="酒店", location="lng,lat", radius=2000) — 搜索景点周边酒店
- maps_schema_personal_map(orgName="{city}行程", lineList=[...]) — 生成高德地图链接（最后调用）

## 约束
- 景点信息必须来自提供的数据，不得编造
- 每天 2-3 个景点，按地理位置相邻分组
- 天气恶劣时给出室内备选
- 人群适配: 婴幼儿→推车友好，老人→避免爬山/长步行
- 最后调用 maps_schema_personal_map 生成高德地图链接"""
```

- [ ] **Step 2: 验证模板可导入**

```bash
cd src && python3 -c "
from agent.trip.prompts import PHASE1_PROMPT, PHASE2_PROMPT, PHASE3_PROMPT
# 验证所有占位符可 format
try:
    p1 = PHASE1_PROMPT.format(city='北京', preferences='历史文化', free_text='')
    print('Phase 1 prompt:', len(p1), 'chars')
    p2 = PHASE2_PROMPT.format(pois='- 故宫 (116.397,39.916)', city='北京')
    print('Phase 2 prompt:', len(p2), 'chars')
    p3 = PHASE3_PROMPT.format(city='北京', days='3', pois='...', routes='...', weather='...', template='# Template', free_text='')
    print('Phase 3 prompt:', len(p3), 'chars')
except KeyError as e:
    print(f'Missing placeholder: {e}')
"
```

- [ ] **Step 3: Commit**

```bash
git add src/agent/trip/prompts.py
git commit -m "feat(trip): add phase-specific prompts (data collection, routing, generation)"
```

---

### Task 5: Hooks 模块 `hooks.py`

**Files:**
- Create: `src/agent/trip/hooks.py`

**Interfaces:**
- Produces: `trip_hooks` (Hooks 实例, id='trip-planner', defer_loading=True), `drain_map_events() -> list[MapEvent]`, `reset_map_bus() -> None`
- Consumes: `agent.trip.events.MapEvent`, `agent.trip.extractors.extract_pois/extract_route/extract_weather`

- [ ] **Step 1: 创建 `hooks.py`**

```python
# src/agent/trip/hooks.py
"""旅行规划 Hooks — 拦截高德 MCP 工具调用，提取坐标注入 SSE 流。
使用 contextvars 而非 ctx.state，因为 ctx.state 生命周期 = agent.run()。
"""
from __future__ import annotations

import contextvars

from pydantic_ai.capabilities import Hooks
from pydantic_ai.tools import RunContext

from .events import MapEvent
from .extractors import extract_pois, extract_route, extract_weather

# ★ contextvars: 跨 agent.run() 调用存活
_map_bus: contextvars.ContextVar[list[MapEvent]] = contextvars.ContextVar(
    "trip_map_events", default=[]
)

trip_hooks = Hooks(
    id="trip-planner",
    description="旅行规划：拦截高德 MCP 工具结果，提取坐标推流前端渲染地图",
    defer_loading=True,
)


# ── POI 搜索拦截 ──────────────────────────────────────────

@trip_hooks.on.after_tool_execute(
    tools=["maps_text_search", "maps_around_search", "maps_search_detail"]
)
async def on_poi_found(ctx: RunContext, *, call, tool_def, args, result):
    """地图搜索完成 → 提取 POI 坐标。"""
    pois = extract_pois(result)
    if not pois:
        return
    events = _map_bus.get()
    events.append(MapEvent(
        type="POI_FOUND",
        markers=[
            {"name": p.name, "lng": p.lng, "lat": p.lat, "address": p.address}
            for p in pois
        ],
    ))


# ── 路线规划拦截 ───────────────────────────────────────────

@trip_hooks.on.after_tool_execute(
    tools=["maps_direction_walking", "maps_direction_driving",
           "maps_direction_transit_integrated", "maps_direction_bicycling"]
)
async def on_route_planned(ctx: RunContext, *, call, tool_def, args, result):
    """路线规划完成 → 提取 polyline。"""
    polyline = extract_route(result)
    if not polyline:
        return
    events = _map_bus.get()
    events.append(MapEvent(
        type="ROUTE_SEGMENT",
        polyline=[{"lng": p[0], "lat": p[1]} for p in polyline],
    ))


# ── 天气查询拦截 ───────────────────────────────────────────

@trip_hooks.on.after_tool_execute(tools=["maps_weather"])
async def on_weather(ctx: RunContext, *, call, tool_def, args, result):
    """天气查询完成 → 提取预报数据。"""
    forecasts = extract_weather(result)
    if not forecasts:
        return
    events = _map_bus.get()
    events.append(MapEvent(type="WEATHER_UPDATE", forecasts=forecasts))


# ── 流事件注入 (Phase 3) ──────────────────────────────────

@trip_hooks.on.run_event_stream
async def inject_map_events(ctx: RunContext, *, stream):
    """在每个标准 AG-UI 事件前，将累积的 MAP_DATA 事件注入流中。"""
    async for event in stream:
        events = _map_bus.get()
        while events:
            yield events.pop(0)
        yield event


# ── Planner 使用的辅助函数 ─────────────────────────────────

def drain_map_events() -> list[MapEvent]:
    """Phase 1/2 非流式阶段：一次性取出并清空所有 MAP_DATA 事件。"""
    events = _map_bus.get()
    result = list(events)
    events.clear()
    return result


def reset_map_bus() -> None:
    """每次 trip planning 请求开始时重置事件总线。"""
    _map_bus.set([])
```

- [ ] **Step 2: 验证 hooks 可导入和注册**

```bash
cd src && python3 -c "
from agent.trip.hooks import trip_hooks, drain_map_events, reset_map_bus
assert trip_hooks.id == 'trip-planner'
reset_map_bus()
events = drain_map_events()
assert events == []
print('Hooks OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add src/agent/trip/hooks.py
git commit -m "feat(trip): add hooks (after_tool_execute + run_event_stream + contextvars bus)"
```

---

### Task 6: Planner 编排器 `planner.py`

**Files:**
- Create: `src/agent/trip/planner.py`

**Interfaces:**
- Produces: `plan_trip(agent: Agent, request: dict) -> AsyncIterable[ProgressEvent | MapEvent | DoneEvent | AgentStreamEvent]`
- Consumes: `agent.trip.schemas.TripData/RouteData`, `agent.trip.prompts.PHASE1/2/3_PROMPT`, `agent.trip.hooks.drain_map_events/reset_map_bus`, `agent.trip.events.ProgressEvent/MapEvent/DoneEvent`

- [ ] **Step 1: 创建 `planner.py`**

```python
# src/agent/trip/planner.py
"""旅行规划编排器 — 同一个通用 Agent 跑 3 次。
Phase 1: run() → TripData
Phase 2: run() → RouteData
Phase 3: run_stream() → SSE 文本 + MAP_DATA 事件
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterable

from pydantic_ai import Agent, UsageLimits

from .events import DoneEvent, ProgressEvent
from .hooks import drain_map_events, reset_map_bus
from .prompts import PHASE1_PROMPT, PHASE2_PROMPT, PHASE3_PROMPT
from .schemas import RouteData, TripData

# itinerary-template.md 路径
_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent
    / "skills" / "travel-planner" / "references" / "itinerary-template.md"
)


async def plan_trip(
    agent: Agent,
    request: dict[str, Any],
) -> AsyncIterable:
    """三阶段旅行规划编排。

    Args:
        agent: 通用 Agent 实例（已注册 toolsets + capabilities）
        request: {
            city: str,
            days: int,
            start_date: str | None,
            preferences: list[str],
            free_text: str,
            message_history: list[ModelMessage],
        }

    Yields:
        ProgressEvent, MapEvent, AgentStreamEvent (AG-UI), DoneEvent
    """
    # 非流式事件（Progress / Map / Done）→ SSE 字符串
    # AG-UI 事件 → pydantic-ai 自动序列化为 SSE
    reset_map_bus()

    city = request["city"]
    days = request.get("days", 3)
    preferences = ", ".join(request.get("preferences", ["景点"]))
    free_text = request.get("free_text", "")
    message_history = request.get("message_history", [])

    # ═══════════════════════════════════════════════════════════
    # Phase 1: 数据收集 → TripData
    # ═══════════════════════════════════════════════════════════
    yield ProgressEvent(
        phase="data_collection", progress=0.1,
        message=f"正在搜索{city}的景点和天气...",
    )

    try:
        phase1_result = await agent.run(
            PHASE1_PROMPT.format(
                city=city,
                preferences=preferences,
                free_text=free_text,
            ),
            message_history=message_history,
            result_type=TripData,
            usage_limits=UsageLimits(request_limit=6, total_tokens_limit=20_000),
        )
        trip_data: TripData = phase1_result.data
    except Exception:
        # Phase 1 失败 → 降级为空数据 + 错误提示
        yield ProgressEvent(
            phase="data_collection", progress=0.33,
            message="数据收集失败，将使用通用信息生成行程",
        )
        trip_data = TripData(
            pois=[
                __class__._fallback_poi(city, 0),
                __class__._fallback_poi(city, 1),
            ],
            weather=[],
        )

    yield from (_e for _e in drain_map_events())
    yield ProgressEvent(
        phase="data_collected", progress=0.33,
        message=f"已找到 {len(trip_data.pois)} 个景点",
    )

    # ═══════════════════════════════════════════════════════════
    # Phase 2: 路线规划 → RouteData
    # ═══════════════════════════════════════════════════════════
    if len(trip_data.pois) >= 2:
        yield ProgressEvent(
            phase="route_planning", progress=0.35,
            message="正在规划景点间路线...",
        )
        # 将 PoiItem 列表格式化为 LLM 可读文本
        pois_text = "\n".join(
            f"{i+1}. {p.name} ({p.lng:.6f}, {p.lat:.6f}) — {p.address}"
            for i, p in enumerate(trip_data.pois)
        )
        try:
            phase2_result = await agent.run(
                PHASE2_PROMPT.format(pois=pois_text, city=city),
                message_history=message_history,
                result_type=RouteData,
                usage_limits=UsageLimits(request_limit=4, total_tokens_limit=10_000),
            )
            route_data: RouteData = phase2_result.data
        except Exception:
            yield ProgressEvent(
                phase="route_planning", progress=0.66,
                message="路线规划失败，跳过",
            )
            route_data = RouteData(routes=[])

        yield from (_e for _e in drain_map_events())
        yield ProgressEvent(
            phase="routes_planned", progress=0.66,
            message=f"已规划 {len(route_data.routes)} 条路线",
        )
    else:
        route_data = RouteData(routes=[])

    # ═══════════════════════════════════════════════════════════
    # Phase 3: 行程生成 → 流式
    # ═══════════════════════════════════════════════════════════
    yield ProgressEvent(
        phase="generating", progress=0.70,
        message="正在生成行程...",
    )

    # 构建 Phase 3 上下文
    pois_text = "\n".join(
        f"- {p.name} | lng={p.lng:.6f} lat={p.lat:.6f} | {p.address} | {p.category}"
        for p in trip_data.pois
    )
    routes_text = "\n".join(
        f"- {r.from_poi} → {r.to_poi} | {r.transport_mode}"
        for r in route_data.routes
    ) if route_data.routes else "（无路线数据）"
    weather_text = "\n".join(
        f"- {w.date} | {w.day_weather} | {w.day_temp}°C / {w.night_temp}°C"
        for w in trip_data.weather
    ) if trip_data.weather else "（无天气数据）"

    # 加载行程模板
    template = ""
    if _TEMPLATE_PATH.exists():
        template = _TEMPLATE_PATH.read_text(encoding="utf-8")

    async with agent.run_stream(
        PHASE3_PROMPT.format(
            city=city,
            days=days,
            pois=pois_text,
            routes=routes_text,
            weather=weather_text,
            template=template,
            free_text=free_text,
        ),
        message_history=message_history,
        usage_limits=UsageLimits(request_limit=4, total_tokens_limit=30_000),
    ) as stream:
        async for event in stream:
            yield event

    yield DoneEvent(progress=1.0)
```

- [ ] **Step 2: 修复 `_fallback_poi` 引用（写在类级别不要用 `__class__`）**

`plan_trip` 是独立函数，没有 `__class__`。将降级场景中的 `__class__._fallback_poi` 替换为内联逻辑：

```python
    # 降级: 生成占位 POI
    trip_data = TripData(
        pois=[
            PoiItem(name=f"{city}热门景点1", lng=116.397, lat=39.916, category="景点"),
            PoiItem(name=f"{city}热门景点2", lng=116.412, lat=39.882, category="景点"),
        ],
        weather=[],
    )
```

- [ ] **Step 3: 验证 planner 可导入**

```bash
cd src && python3 -c "
from agent.trip.planner import plan_trip
import inspect
assert inspect.isasyncgenfunction(plan_trip), 'plan_trip should be an async generator function'
print('Planner OK')
"
```

- [ ] **Step 4: Commit**

```bash
git add src/agent/trip/planner.py
git commit -m "feat(trip): add 3-phase planner (data collection → routing → streaming generation)"
```

---

### Task 7: Agent 注册 `model_client.py`

**Files:**
- Modify: `src/agent/model_client.py:48-50`

- [ ] **Step 1: 在 `init_agent()` 中注册 `trip_hooks`**

找到 `src/agent/model_client.py` 中 `init_agent` 函数的这段代码（约第 48-50 行）：

```python
    if registry is not None:
        toolsets.append(SkillToolset(registry))
    agent = Agent(model, toolsets=toolsets)
```

修改为：

```python
    if registry is not None:
        toolsets.append(SkillToolset(registry))

    from agent.trip.hooks import trip_hooks

    agent = Agent(model, toolsets=toolsets, capabilities=[trip_hooks])
```

- [ ] **Step 2: 验证 Agent 创建不报错**

```bash
cd src && python3 -c "
from agent.model_client import init_agent
agent = init_agent()
print(f'Agent created: {agent}')
print(f'Capabilities: {agent._capabilities}')
"
```

- [ ] **Step 3: Commit**

```bash
git add src/agent/model_client.py
git commit -m "feat(trip): register trip_hooks as agent capability"
```

---

### Task 8: 意图检测 + Planner 路由 `agent_service.py`

**Files:**
- Modify: `src/services/agent_service.py:134-144`（`stream_ag_ui` 方法开头）

- [ ] **Step 1: 在 `AgentService` 类中添加意图检测辅助方法**

```python
# 在 AgentService 类的末尾添加（或作为模块级函数）

_TRIP_KEYWORDS = [
    "几日游", "攻略", "行程", "去哪玩", "旅游规划", "旅游",
    "玩几天", "怎么玩", "景点推荐", "去.*旅游",
]

import re

def _is_trip_intent(message: str) -> bool:
    """检测用户消息是否包含旅行规划意图。"""
    for kw in _TRIP_KEYWORDS:
        if re.search(kw, message):
            return True
    return False

def _extract_city(message: str) -> str | None:
    """从消息中提取城市名（简单正则，可后续增强）。"""
    import re
    # 常见模式: "北京三日游" → 北京, "去杭州" → 杭州
    # 简单实现: 匹配常见中文城市名
    cities = ["北京", "上海", "广州", "深圳", "杭州", "成都", "重庆", "西安",
              "南京", "武汉", "长沙", "厦门", "青岛", "大连", "苏州", "昆明",
              "丽江", "三亚", "桂林", "张家界", "拉萨", "哈尔滨", "长春", "沈阳"]
    for city in cities:
        if city in message:
            return city
    return None
```

- [ ] **Step 2: 在 `stream_ag_ui` 开头插入意图路由**

在 `stream_ag_ui` 的 `user_content` 提取之后（约第 176 行），在流前 DB 写入之前，加入路由逻辑：

```python
        # ② 提取用户输入文本
        user_content = ""
        for msg in run_input.messages:
            if msg.role == "user":
                if isinstance(msg.content, str):
                    user_content = msg.content
                elif isinstance(msg.content, list):
                    for item in msg.content:
                        user_content += getattr(item, "text", "") or getattr(item, "content", "") or ""

        # ★★★ 旅行规划意图路由 ★★★
        if _is_trip_intent(user_content):
            city = _extract_city(user_content)
            if city:
                try:
                    from agent.trip.planner import plan_trip

                    async def _trip_sse_stream():
                        agent = get_agent()
                        request_data = {
                            "city": city,
                            "days": 3,  # 默认 3 天，后续可 NLP 提取
                            "start_date": None,
                            "preferences": [],
                            "free_text": user_content,
                            "message_history": [],
                        }
                        async for event in plan_trip(agent, request_data):
                            if isinstance(event, (ProgressEvent, MapEvent, DoneEvent)):
                                yield event.to_sse()
                            else:
                                yield event  # AG-UI 标准事件

                    return StreamingResponse(
                        _trip_sse_stream(),
                        media_type="text/event-stream",
                    )
                except Exception as exc:
                    logger.exception("Trip planner failed, falling back to normal chat: %s", exc)

        # ★★★ 继续现有通用对话流程 ★★★

        # ③ 流前 DB 写入：创建/获取会话 + 创建 run 记录
        conv, _ = await self.conv_repo.get_or_create(...)
```

注意：需要补充 import：

```python
# 文件顶部 import 区域添加
from agent.trip.events import ProgressEvent, MapEvent, DoneEvent
from agent.trip.planner import plan_trip
```

- [ ] **Step 3: 验证语法正确**

```bash
cd src && python3 -c "
from services.agent_service import AgentService
print('Import OK')
"
```

- [ ] **Step 4: Commit**

```bash
git add src/services/agent_service.py
git commit -m "feat(agent_service): add trip intent detection and planner routing"
```

---

### Task 9: SKILL.md 降级路径

**Files:**
- Modify: `src/agent/skills/travel-planner/SKILL.md`

- [ ] **Step 1: 重写 `SKILL.md` 为降级路径专用**

将现有内容替换为：

```markdown
---
name: travel-planner
description: >
  [降级路径] 多日旅游行程规划。当 planner 编排器不可用时，Agent 通过此 skill 独立完成。
  触发词: "几日游""攻略""行程安排""去哪玩""旅游规划"。
  纯闲聊、查天气、查门票、查单个景点信息不适用。
auto_load_references:
  - references/itinerary-template.md
---

## 旅行规划工作流

### 第一步：确认信息
确认目的地、天数、日期。缺失的问一次，不答跳过。

### 第二步：收集数据
1. maps_text_search(keywords="<偏好>", city="<城市>") — 搜索 6-8 个热门景点
2. maps_weather(city="<城市>") — 查询天气

### 第三步：规划路线
对列表中相邻景点，调用 maps_direction_walking（近距离）或 maps_direction_transit_integrated（远距离）
origin 和 destination 参数使用 "lng,lat" 格式。

### 第四步：生成行程 + 补充信息
综合所有数据，按 `references/itinerary-template.md` 结构输出每日行程。
- 调用 maps_around_search 搜索景点周边餐饮和酒店
- 输出完行程后调用 maps_schema_personal_map 生成高德地图链接

## 约束
- 景点名称、地址、坐标必须来自工具返回，不得编造
- 每天 2-3 个景点，位置相邻
- 天气恶劣给出室内备选
- 人群适配: 婴幼儿→推车友好，老人→避免爬山/长步行
```

- [ ] **Step 2: Commit**

```bash
git add src/agent/skills/travel-planner/SKILL.md
git commit -m "docs(travel-planner): rewrite SKILL.md as fallback path"
```

---

### Task 10: 前端 SSE 事件解析 `stream-client.ts`

**Files:**
- Modify: `web/src/api/stream-client.ts`

- [ ] **Step 1: 新增类型定义**

在文件顶部 `ImagePayload` 接口之后添加：

```typescript
// web/src/api/stream-client.ts

export interface MapMarker {
  name: string
  lng: number
  lat: number
  address?: string
}

export interface MapPolylinePoint {
  lng: number
  lat: number
}

export interface MapData {
  type: 'POI_FOUND' | 'ROUTE_SEGMENT' | 'WEATHER_UPDATE'
  markers?: MapMarker[]
  polyline?: MapPolylinePoint[]
  forecasts?: Array<{
    date: string
    day_weather: string
    day_temp: string
    night_temp: string
  }>
}

export interface ProgressData {
  phase: string
  progress: number
  message: string
}
```

- [ ] **Step 2: 在 `StreamClientOptions` 接口中新增回调**

```typescript
export interface StreamClientOptions {
  // ... 现有选项保持不变 ...
  onMapData?: (data: MapData) => void        // ★ 新增
  onProgress?: (data: ProgressData) => void   // ★ 新增
}
```

- [ ] **Step 3: 在 `onmessage` 处理中新增事件解析**

在 `streamClient` 函数的 `onmessage` 回调中，`RUN_ERROR` 处理后、兜底 `data.content` 前插入：

```typescript
        // ── 地图数据（自定义事件） ──
        if (type === 'MAP_DATA') {
          const payload = data.payload as MapData | undefined
          if (payload) {
            options.onMapData?.(payload)
          }
          return
        }

        // ── 阶段进度（自定义事件） ──
        if (type === 'PROGRESS') {
          options.onProgress?.({
            phase: data.phase || '',
            progress: data.progress || 0,
            message: data.message || '',
          })
          return
        }

        // ── 行程完成（自定义事件） ──
        if (type === 'TRIP_DONE') {
          return
        }
```

- [ ] **Step 4: 验证 TypeScript 编译**

```bash
cd web && pnpm run typecheck
```

- [ ] **Step 5: Commit**

```bash
git add web/src/api/stream-client.ts
git commit -m "feat(stream-client): parse MAP_DATA, PROGRESS, TRIP_DONE custom SSE events"
```

---

### Task 11: 前端状态管理 `useChatStream.ts`

**Files:**
- Modify: `web/src/composables/useChatStream.ts`

- [ ] **Step 1: 新增响应式状态**

在 `useChatStream()` 函数内的现有 ref 声明区域添加：

```typescript
  // ★ 地图增量渲染状态
  const mapMarkers = ref<MapMarker[]>([])
  const mapPolylines = ref<MapPolylinePoint[][]>([])
  const tripProgress = ref<ProgressData | null>(null)

  // 导入类型（文件顶部添加）
  import type { MapMarker, MapPolylinePoint, MapData, ProgressData } from '@/api/stream-client'
```

- [ ] **Step 2: 新增 `onMapData` 和 `onProgress` 处理器**

在 `generate()` 函数的 `streamClient` 调用中添加两个新的回调：

```typescript
          onMapData(data) {
            if (!isCurrent()) return
            if (data.type === 'POI_FOUND' && data.markers) {
              mapMarkers.value.push(...data.markers)
            }
            if (data.type === 'ROUTE_SEGMENT' && data.polyline) {
              mapPolylines.value.push(data.polyline)
            }
          },
          onProgress(data) {
            if (!isCurrent()) return
            tripProgress.value = data
          },
```

- [ ] **Step 3: 在 `clear()` 函数中重置地图状态**

```typescript
  function clear() {
    // ... 现有清理逻辑 ...
    mapMarkers.value = []
    mapPolylines.value = []
    tripProgress.value = null
  }
```

- [ ] **Step 4: 在返回对象中暴露新状态**

```typescript
  return {
    // ... 现有返回 ...
    mapMarkers,      // ★
    mapPolylines,    // ★
    tripProgress,    // ★
  }
```

- [ ] **Step 5: 验证编译**

```bash
cd web && pnpm run typecheck
```

- [ ] **Step 6: Commit**

```bash
git add web/src/composables/useChatStream.ts
git commit -m "feat(useChatStream): add mapMarkers/mapPolylines/tripProgress reactive state"
```

---

### Task 12: 前端地图增量渲染 `TravelMap.vue`

**Files:**
- Modify: `web/src/components/TravelMap.vue`

- [ ] **Step 1: 将现有 `watch` 全量重绘改为增量模式**

当前 `TravelMap.vue` 在 `watch(props.routes, ...)` 中执行 `clearOverlays()` + 完整 `renderCurrentMap()`，破坏增量体验。

**修改方案**：新增一个 `renderIncremental` 逻辑，在已有地图实例上通过 `map.add()` 增量添加标记，而不销毁重建。

```typescript
// TravelMap.vue — 增量渲染逻辑（追加到现有 <script setup> 中）

const props = defineProps<{
  routes: RoutePayload[]           // 现有 prop — 保留兼容
  markers?: MapMarker[]            // ★ 增量 markers
  polylines?: MapPolylinePoint[][] // ★ 增量 polylines
}>()

// ★ 增量 markers watcher
watch(
  () => props.markers?.length ?? 0,
  async (newLen, oldLen) => {
    if (!mapInstance || !props.markers) return
    const added = props.markers.slice(oldLen || 0)
    if (added.length === 0) return

    try {
      await loadAmap()
      const AMap = window.AMap

      for (const m of added) {
        const marker = new AMap.Marker({
          map: mapInstance,
          position: [m.lng, m.lat],
          title: m.name,
          label: {
            content: `<div style="background:#4A6FA5;color:#fff;padding:2px 8px;border-radius:12px;font-size:12px;white-space:nowrap">${m.name}</div>`,
            offset: new AMap.Pixel(0, -30),
          },
        })
        overlays.push(marker)
      }

      // 自适应视野
      if (props.markers.length > 1) {
        mapInstance.setFitView(null, false, [60, 40, 60, 40])
        nextTick(() => {
          if (mapInstance && mapInstance.getZoom() > 16) {
            mapInstance.setZoom(14)
          }
        })
      }
    } catch (e: any) {
      console.error('[TravelMap] incremental marker render error:', e)
    }
  },
)

// ★ 增量 polylines watcher
watch(
  () => props.polylines?.length ?? 0,
  async (newLen, oldLen) => {
    if (!mapInstance || !props.polylines) return
    const added = props.polylines.slice(oldLen || 0)
    if (added.length === 0) return

    try {
      await loadAmap()
      const AMap = window.AMap

      for (const segment of added) {
        const path: [number, number][] = segment.map(
          (p) => [p.lng, p.latitude ?? p.lat]
        )
        if (path.length < 2) continue

        // Border (white stroke)
        const borderLine = new AMap.Polyline({
          map: mapInstance,
          path,
          strokeColor: '#fff',
          strokeWeight: 8,
          strokeOpacity: 1,
          lineJoin: 'round',
          lineCap: 'round',
          zIndex: 1,
        })
        overlays.push(borderLine)

        // Main line
        const polyline = new AMap.Polyline({
          map: mapInstance,
          path,
          strokeColor: '#4A6FA5',
          strokeWeight: 5,
          strokeOpacity: 0.9,
          lineJoin: 'round',
          lineCap: 'round',
          zIndex: 2,
          showDir: true,
        })
        overlays.push(polyline)
      }
    } catch (e: any) {
      console.error('[TravelMap] incremental polyline render error:', e)
    }
  },
)
```

- [ ] **Step 2: 在聊天页面中传入新的 props**

在 `chat/index.vue` 中（或使用 `TravelMap` 的页面），将 `useChatStream` 暴露的 `mapMarkers`、`mapPolylines` 传给 `TravelMap`：

```vue
<TravelMap
  :routes="chatStore.currentRoutes"
  :markers="mapMarkers"
  :polylines="mapPolylines"
/>
```

- [ ] **Step 3: 验证编译**

```bash
cd web && pnpm run typecheck
```

- [ ] **Step 4: Commit**

```bash
git add web/src/components/TravelMap.vue web/src/pages/chat/index.vue
git commit -m "feat(TravelMap): add incremental marker/polyline rendering via watchers"
```

---

### Task 13: 端到端验证

- [ ] **Step 1: 启动后端**

```bash
cd fastapi-agent-pydanticai && source .venv/bin/activate && python run.py
```

- [ ] **Step 2: 启动前端**

```bash
cd web && pnpm run dev
```

- [ ] **Step 3: 验证旅行规划流程**

1. 打开 `http://localhost:3000`，登录
2. 新建会话，输入"帮我规划北京三日游"
3. 验证：
   - Phase 1: 看到进度条"正在搜索北京的景点和天气..."
   - 地图上出现 POI 标记（6-8 个景点）
   - Phase 2: 进度条"正在规划景点间路线..."
   - 地图上出现路线连线
   - Phase 3: 进度条"正在生成行程..."，随后聊天文本流式输出
   - 文本输出过程中，餐饮 POI 增量出现在地图上

- [ ] **Step 4: 验证通用对话不受影响**

在另一个会话中输入"你好"、"今天天气怎么样"等普通消息，确认：
- 正常回复，无报错
- 不会误触发旅行规划

- [ ] **Step 5: 验证降级路径**

停止后端，修改 `agent_service.py` 使 planner 路由抛异常，重启：
- 输入"帮我规划北京三日游"
- 确认 Agent 通过 SKILL.md 降级路径完成规划（无阶段进度，但能产出结果）

- [ ] **Step 6: Commit 并打 tag**

```bash
git add -A
git commit -m "chore: e2e validation notes"
git tag trip-planner-v1.0
```
