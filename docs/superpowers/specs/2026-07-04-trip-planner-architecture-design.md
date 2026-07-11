# 旅行规划场景架构设计

> 日期: 2026-07-04 | 状态: 待评审 | 版本: 3.1 (修复 reviews + 新增 ADR)

## 1. 设计目标

在通用聊天 Agent 架构之上，以**零耦合**方式接入旅行规划场景：

1. 单 Agent、分阶段编排，阶段间用 `result_type` 传递结构化数据
2. 只有最终阶段流式输出文本 + 增量地图渲染
3. 旅行规划模块独立，与通用对话互不干扰

---

## 2. 核心架构

### 2.1 三阶段编排

同一个通用 Agent 跑 3 次 `run()`，不是 3 个 Agent。

```
用户: "帮我规划北京三日游"
         │
         ▼
  ┌──────────────────────────────────────────────────┐
  │              Planner (纯函数，不调 LLM)            │
  │                                                    │
  │  Phase 1: run()  → TripData (强类型)              │
  │  ├─ 工具: maps_text_search, maps_weather          │
  │  └─ ~2s, 上下文极小                                 │
  │         │                                          │
  │         ├──▶ ProgressEvent  → SSE                  │
  │         ├──▶ MapEvent (POI) → SSE                  │
  │                                                    │
  │  Phase 2: run()  → RouteData (强类型)             │
  │  ├─ 输入: TripData.pois                           │
  │  ├─ 工具: maps_direction_*                         │
  │  └─ ~2s, 上下文极小                                 │
  │         │                                          │
  │         ├──▶ ProgressEvent        → SSE            │
  │         ├──▶ MapEvent (polyline)  → SSE            │
  │                                                    │
  │  Phase 3: run_stream()  → SSE 文本 + 增量地图       │
  │  ├─ 输入: TripData + RouteData                     │
  │  ├─ 工具: maps_around_search (按需)                 │
  │  └─ ~15s, 上下文 = 结构化数据 + 模板                 │
  │         │                                          │
  │         └──▶ 文本 delta + MapEvent + RUN_FINISHED   │
  └──────────────────────────────────────────────────┘
```

### 2.2 为什么不是一次长 run

单次 run 需要 7-8 步工具调用 + 长指令 → 上下文膨胀、步骤遗漏、失败全部重试、用户黑盒等待。

### 2.3 解耦：Hooks + contextvars

- `defer_loading=True`：旅行 hooks 默认不激活，零开销
- `tools=[...]` 过滤：精确拦截 `maps_text_search`、`maps_direction_*`
- **`contextvars.ContextVar`** 而非 `ctx.state`：hooks 和 planner 共享事件总线，跨 `agent.run()` 生命周期存活

---

## 3. 模块结构

```
src/agent/
├── skills/
│   └── travel-planner/
│       ├── SKILL.md                      # 降级用（见 §2.4 附录）
│       └── references/
│           └── itinerary-template.md
│
├── trip/                                 # ★ 新增
│   ├── __init__.py
│   ├── hooks.py                          # Hooks + contextvars 事件总线
│   ├── planner.py                        # 三阶段编排器
│   ├── prompts.py                        # Phase 1/2/3 指令
│   ├── schemas.py                        # TripData, RouteData
│   ├── events.py                         # MapEvent, ProgressEvent
│   └── extractors.py                     # MCP 结果解析
│
├── mcp.py                                # 不变
├── model_client.py                       # ★ +1 行：capabilities=[trip_hooks]
└── skills/toolset.py                     # 不变

src/
├── services/
│   └── agent_service.py                  # ★ 意图检测 → planner 路由

web/src/
├── api/stream-client.ts                  # ★ MAP_DATA + PROGRESS 事件
├── composables/useChatStream.ts          # ★ mapEvents + progress 状态
└── components/TravelMap.vue              # ★ 增量渲染
```

---

## 4. 核心组件设计

### 4.1 事件总线（`contextvars` 替代 `ctx.state`）

```python
# src/agent/trip/hooks.py

import contextvars
from pydantic_ai.capabilities import Hooks
from pydantic_ai.tools import RunContext

# ★ 使用 contextvars 而非 ctx.state：
# ctx.state 生命周期 = agent.run()，函数返回后销毁。
# contextvars 跨 agent.run() 调用存活，planner 可以在 run 结束后 drain。
_map_bus: contextvars.ContextVar[list] = contextvars.ContextVar(
    'trip_map_events', default=[]
)

trip_hooks = Hooks(
    id='trip-planner',
    description='旅行规划：拦截高德 MCP 工具结果，提取坐标推流前端渲染地图',
    defer_loading=True,
)

@trip_hooks.on.after_tool_execute(
    tools=['maps_text_search', 'maps_around_search', 'maps_search_detail']
)
async def on_poi_found(ctx: RunContext, *, call, tool_def, args, result):
    from .extractors import extract_pois
    from .events import MapEvent
    pois = extract_pois(result)
    if not pois:
        return
    events = _map_bus.get()
    events.append(MapEvent(
        type='POI_FOUND',
        markers=[{'name': p.name, 'lng': p.lng, 'lat': p.lat, 'address': p.address}
                 for p in pois],
    ))

@trip_hooks.on.after_tool_execute(
    tools=['maps_direction_walking', 'maps_direction_driving',
           'maps_direction_transit_integrated', 'maps_direction_bicycling']
)
async def on_route_planned(ctx: RunContext, *, call, tool_def, args, result):
    from .extractors import extract_route
    from .events import MapEvent
    polyline = extract_route(result)
    if not polyline:
        return
    events = _map_bus.get()
    events.append(MapEvent(
        type='ROUTE_SEGMENT',
        polyline=[{'lng': p[0], 'lat': p[1]} for p in polyline],
    ))

@trip_hooks.on.after_tool_execute(tools=['maps_weather'])
async def on_weather(ctx: RunContext, *, call, tool_def, args, result):
    from .extractors import extract_weather
    from .events import MapEvent
    forecasts = extract_weather(result)
    if not forecasts:
        return
    events = _map_bus.get()
    events.append(MapEvent(type='WEATHER_UPDATE', forecasts=forecasts))

@trip_hooks.on.run_event_stream
async def inject_map_events(ctx: RunContext, *, stream):
    """Phase 3 流式阶段：在每个标准事件前注入累积的 MAP_DATA。"""
    async for event in stream:
        events = _map_bus.get()
        while events:
            yield events.pop(0)
        yield event


def drain_map_events() -> list:
    """Phase 1/2 非流式阶段：一次性取出 MAP_DATA 事件。"""
    events = _map_bus.get()
    result = list(events)
    events.clear()
    return result

def reset_map_bus():
    """每次 trip planning 请求开始时清空总线。"""
    _map_bus.set([])
```

### 4.2 编排器（`src/agent/trip/planner.py`）

```python
"""旅行规划编排器 — 同一个 Agent 跑 3 次。"""

from pydantic_ai import Agent, UsageLimits
from .schemas import TripData, RouteData
from .prompts import PHASE1_PROMPT, PHASE2_PROMPT, PHASE3_PROMPT
from .hooks import drain_map_events, reset_map_bus
from .events import ProgressEvent, DoneEvent


async def plan_trip(
    agent: Agent,
    request: dict,  # { city, days, start_date, preferences, free_text, message_history }
) -> AsyncIterable:
    """三阶段旅行规划编排。"""

    reset_map_bus()
    message_history = request.get('message_history', [])

    # ═══════════════════════════════════════════════════════════
    # Phase 1: 数据收集 → TripData
    # ═══════════════════════════════════════════════════════════
    yield ProgressEvent(phase='data_collection', progress=0.1,
                        message='正在搜索景点和天气...')

    phase1_result = await agent.run(
        PHASE1_PROMPT.format(
            city=request['city'],
            preferences=', '.join(request.get('preferences', ['景点'])),
            free_text=request.get('free_text', ''),
        ),
        message_history=message_history,  # ★ 携带对话上下文
        result_type=TripData,
        # result_type 模式下，prompt 需要明确要求"只返回 JSON"，
        # 因此 PHASE1_PROMPT 末尾有格式约束
        usage_limits=UsageLimits(request_limit=6, total_tokens_limit=20_000),
    )

    trip_data: TripData = phase1_result.data
    yield from drain_map_events()
    yield ProgressEvent(phase='data_collected', progress=0.33,
                        message=f'已找到 {len(trip_data.pois)} 个景点')

    # ═══════════════════════════════════════════════════════════
    # Phase 2: 路线规划 → RouteData
    # ═══════════════════════════════════════════════════════════
    if len(trip_data.pois) >= 2:  # ★ 只有 1 个景点时跳过
        yield ProgressEvent(phase='route_planning', progress=0.35,
                            message='正在规划景点间路线...')

        phase2_result = await agent.run(
            PHASE2_PROMPT.format(
                pois=trip_data.pois,
                city=request['city'],
            ),
            message_history=message_history,
            result_type=RouteData,
            usage_limits=UsageLimits(request_limit=4, total_tokens_limit=10_000),
        )

        route_data: RouteData = phase2_result.data
        yield from drain_map_events()
        yield ProgressEvent(phase='routes_planned', progress=0.66,
                            message=f'已规划 {len(route_data.routes)} 条路线')
    else:
        route_data = RouteData(routes=[])

    # ═══════════════════════════════════════════════════════════
    # Phase 3: 行程生成 → 流式
    # ═══════════════════════════════════════════════════════════
    yield ProgressEvent(phase='generating', progress=0.70,
                        message='正在生成行程...')

    # ★ 从 references 加载模板
    from pathlib import Path
    template_path = Path(__file__).parent.parent / 'skills' / 'travel-planner' / 'references' / 'itinerary-template.md'
    template = template_path.read_text(encoding='utf-8')

    async with agent.run_stream(
        PHASE3_PROMPT.format(
            city=request['city'],
            days=request['days'],
            pois=trip_data.pois,
            routes=route_data.routes,
            weather=trip_data.weather,
            template=template,               # ★ 模板内容直接注入
            free_text=request.get('free_text', ''),
        ),
        message_history=message_history,
        usage_limits=UsageLimits(request_limit=4, total_tokens_limit=30_000),
    ) as stream:
        async for event in stream:
            yield event

    yield DoneEvent(progress=1.0)
```

### 4.2.1 上下文传递详解

每次 `agent.run()` 是一个独立的 LLM 会话。以下是一个旅行规划请求中各阶段的完整上下文构成：

```
                         ┌─────────────────────────┐
                         │  用户原始请求 + 历史消息    │
                         │  message_history (list)   │
                         │  + city, days, preferences │
                         └───────────┬─────────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
              ▼                      ▼                      ▼
   ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
   │     Phase 1       │  │     Phase 2       │  │     Phase 3       │
   │   agent.run()     │  │   agent.run()     │  │  agent.run_stream()│
   │                    │  │                    │  │                    │
   │ 发送给 LLM:        │  │ 发送给 LLM:        │  │ 发送给 LLM:        │
   │ ┌──────────────┐  │  │ ┌──────────────┐  │  │ ┌──────────────┐  │
   │ │ system_prompt │  │  │ │ system_prompt │  │  │ │ system_prompt │  │
   │ │ (通用身份)     │  │  │ │ (通用身份)     │  │  │ │ (通用身份)     │  │
   │ ├──────────────┤  │  │ ├──────────────┤  │  │ ├──────────────┤  │
   │ │ PHASE1_PROMPT│  │  │ │ PHASE2_PROMPT│  │  │ │ PHASE3_PROMPT│  │
   │ │ "搜景点+天气"  │  │  │ │ "规划路线"    │  │  │ │ "生成行程"    │  │
   │ ├──────────────┤  │  │ ├──────────────┤  │  │ ├──────────────┤  │
   │ │ history      │  │  │ │ history      │  │  │ │ history      │  │
   │ │ (用户对话)    │  │  │ │ (用户对话)    │  │  │ │ (用户对话)    │  │
   │ └──────────────┘  │  │ ├──────────────┤  │  │ ├──────────────┤  │
   │                    │  │ │ pois 数据     │  │  │ │ pois 数据     │  │
   │ 工具调用:           │  │ │ (Phase 1 输出)│  │  │ │ (Phase 1 输出)│  │
   │ maps_text_search   │  │ └──────────────┘  │  │ ├──────────────┤  │
   │ maps_weather       │  │                    │  │ │ routes 数据   │  │
   │                    │  │ 工具调用:           │  │ │ (Phase 2 输出)│  │
   │ 输出:               │  │ maps_direction_*   │  │ ├──────────────┤  │
   │ TripData (Pydantic) │  │                    │  │ │ weather 数据  │  │
   └────────┬─────────┘  │ 输出:               │  │ │ (Phase 1 输出)│  │
            │             │ RouteData (Pydantic) │  │ ├──────────────┤  │
            │             └────────┬─────────┘  │ │ template     │  │
            │                      │             │ │ (行程模板)    │  │
            │                      │             │ └──────────────┘  │
            │                      │             │                    │
            │                      │             │ 工具调用:           │
            │                      │             │ maps_around_search  │
            │                      │             │                     │
            │                      │             │ 输出:               │
            │                      │             │ 流式文本 (SSE)       │
            └──────────────────────┴─────────────┘
```

**逐项说明**：

| 上下文元素 | Phase 1 | Phase 2 | Phase 3 | 传递方式 |
|-----------|:---:|:---:|:---:|---------|
| `system_prompt`（Agent 通用身份） | ✅ | ✅ | ✅ | Agent 初始化时固定，每次 run 自动携带 |
| `instructions`（阶段专用指令） | PHASE1_PROMPT | PHASE2_PROMPT | PHASE3_PROMPT | `agent.run(instructions=...)` 参数 |
| `message_history`（用户原始对话） | ✅ | ✅ | ✅ | `agent.run(message_history=...)` 参数，三者相同 |
| TripData（景点 + 天气） | ❌ 产出 | 只传 `pois` | 全部 `pois + weather` | Phase 1 → planner 内存 → Phase 2/3 的 prompt 文本 |
| RouteData（路线） | ❌ | ❌ 产出 | 全部 `routes` | Phase 2 → planner 内存 → Phase 3 的 prompt 文本 |
| `itinerary-template.md` | ❌ | ❌ | ✅ | planner 从文件读取 → Phase 3 的 prompt 文本 |
| Phase 1 的工具调用历史 | — | ❌ 不传递 | ❌ 不传递 | 刻意丢弃，Phase 3 只需要结构化结果 |
| Phase 2 的工具调用历史 | — | — | ❌ 不传递 | 同上 |
| `result_type` schema | TripData | RouteData | 无（流式文本） | pydantic-ai 自动校验 |

**为什么各阶段不共享工具调用历史？**

Phase 1 调用 `maps_text_search("故宫")` → 返回原始 JSON → LLM 提取为 TripData。如果 Phase 3 看到这段原始工具调用历史，LLM 需要重新解析原始 JSON，这是冗余工作。**TripData 已经是 LLM 整理好的结构化数据了**，Phase 3 直接读字段即可。

Phase 2 同理——RouteData.routes 是整理好的路线列表，Phase 3 不需要看到原始的 `maps_direction_walking` 返回的 JSON。

**message_history 在各阶段中的作用**：

`message_history` 是用户在触发旅行规划**之前**的对话历史，例如：

```
user: "你好"
assistant: "你好！有什么可以帮你的？"
user: "帮我规划北京三日游"         ← 触发 planner
```

这段历史传给 Phase 1/2/3 的 `agent.run()`，让 LLM 了解对话上下文（用户偏好、之前的讨论等）。Phase 1 和 Phase 2 之间的工具调用不追加到 `message_history` 中——每次 run 都是独立的会话，`message_history` 保持为触发前的原始对话。

**数据在阶段间的实际格式**：

```python
# Phase 1 输出 → Phase 2/3 输入
trip_data = TripData(
    pois=[
        PoiItem(name="故宫", lng=116.397, lat=39.916, address="东城区景山前街4号"),
        PoiItem(name="天坛", lng=116.412, lat=39.882, address="东城区天坛路"),
        ...
    ],
    weather=[
        WeatherItem(date="2026-07-05", day_weather="晴", day_temp="32", night_temp="22"),
        ...
    ]
)

# 传给 Phase 2 时，格式化为 prompt 文本：
# "景点列表：
#  1. 故宫 (116.397, 39.916) — 东城区景山前街4号
#  2. 天坛 (116.412, 39.882) — 东城区天坛路"

# 传给 Phase 3 时，格式化为 prompt 文本：
# "## 景点数据
#  - 故宫 | lng=116.397 lat=39.916 | 东城区景山前街4号
#  - 天坛 | lng=116.412 lat=39.882 | 东城区天坛路
#  ## 路线数据
#  - 故宫 → 天坛 | walking | [[116.397,39.916], [116.398,39.915], ...]
#  ## 天气数据
#  - 2026-07-05 | 晴 | 32°C / 22°C"
```

### 4.3 阶段指令（`src/agent/trip/prompts.py`）

```python
"""每阶段指令 — 短、聚焦、单一任务。"""

PHASE1_PROMPT = """你是一个数据收集助手。唯一任务：调用工具获取数据。

1. maps_text_search(keywords="{preferences}", city="{city}") — 搜索景点
2. maps_weather(city="{city}") — 查询天气
{free_text_section}

完成后整理为以下 JSON 格式。只返回 JSON，不要任何解释或建议。
```json
{{
  "pois": [
    {{"name": "...", "lng": 116.397, "lat": 39.916, "address": "...", "category": "..."}}
  ],
  "weather": [
    {{"date": "YYYY-MM-DD", "day_weather": "晴", "day_temp": "25", "night_temp": "15"}}
  ]
}}
```
约束：
- POI 的 lng/lat 必须是数字，来自工具返回结果
- 搜索 6-8 个相关景点
- 偏好: {preferences}
额外要求: {free_text}"""

PHASE2_PROMPT = """你是一个路线规划助手。对景点列表中的**相邻景点对**（按列表顺序），规划交通路线。

景点列表：
{pois}

城市：{city}

对列表中每对相邻景点 (pois[0]→pois[1], pois[1]→pois[2], ...)：
- 调用 maps_direction_walking 或 maps_direction_transit_integrated
- 参数 origin 和 destination 使用 "lng,lat" 格式

完成后返回以下 JSON。只返回 JSON，不要任何解释。
```json
{{
  "routes": [
    {{
      "from_poi": "故宫",
      "to_poi": "天坛",
      "transport_mode": "walking",
      "polyline": [[116.397, 39.916], [116.398, 39.915]]
    }}
  ]
}}
```
约束：
- polyline 必须是坐标数组，来自工具返回结果
- transport_mode 为 walking / transit / driving 之一"""

PHASE3_PROMPT = """你是专业旅行规划师。根据提供的数据生成 {city}{days} 日游行程。

## 景点数据
{pois}

## 路线数据
{routes}

## 天气数据
{weather}

## 输出模板（严格遵循此结构）
{template}

## 额外要求
{free_text}

可用工具：
- maps_around_search(keywords="餐饮", location="lng,lat", radius=1000) — 搜索景点周边餐饮
- maps_schema_personal_map(orgName="{city}行程", lineList=[...]) — 生成高德地图链接

约束：
- 景点信息必须来自提供的数据，不得编造
- 每天 2-3 个景点，按地理位置相邻分组
- 天气恶劣时给出室内备选
- 最后调用 maps_schema_personal_map 生成高德地图链接"""
```

### 4.4 Schema（`src/agent/trip/schemas.py`）

```python
"""Phase 1/2 的 result_type。"""
from pydantic import BaseModel

class PoiItem(BaseModel):
    name: str
    lng: float
    lat: float
    address: str = ""
    category: str = ""

class WeatherItem(BaseModel):
    date: str
    day_weather: str
    day_temp: str
    night_temp: str = ""

class TripData(BaseModel):
    pois: list[PoiItem]
    weather: list[WeatherItem]

class RouteSegment(BaseModel):
    from_poi: str
    to_poi: str
    transport_mode: str
    polyline: list[list[float]] = []

class RouteData(BaseModel):
    routes: list[RouteSegment]
```

### 4.5 事件模型（`src/agent/trip/events.py`）

```python
from dataclasses import dataclass, field
import json

@dataclass
class ProgressEvent:
    phase: str
    progress: float
    message: str

    def to_sse(self) -> str:
        return f"data: {json.dumps({'type': 'PROGRESS', 'phase': self.phase, 'progress': self.progress, 'message': self.message}, ensure_ascii=False)}\n\n"

@dataclass
class MapEvent:
    type: str  # POI_FOUND | ROUTE_SEGMENT | WEATHER_UPDATE
    markers: list[dict] = field(default_factory=list)
    polyline: list[dict] = field(default_factory=list)
    forecasts: list[dict] = field(default_factory=list)

    def to_sse(self) -> str:
        return f"data: {json.dumps({'type': 'MAP_DATA', 'payload': self.__dict__}, ensure_ascii=False)}\n\n"

@dataclass
class DoneEvent:
    progress: float = 1.0

    def to_sse(self) -> str:
        return f"data: {json.dumps({'type': 'TRIP_DONE', 'progress': self.progress}, ensure_ascii=False)}\n\n"
```

### 4.6 Hooks（见 §4.1，已含修复）

### 4.7 提取器（`src/agent/trip/extractors.py`）

```python
"""高德 MCP 工具结果解析。"""
from dataclasses import dataclass
from typing import Any
import json

@dataclass
class PoiInfo:
    name: str
    lng: float
    lat: float
    address: str = ""

def extract_pois(result: Any) -> list[PoiInfo]:
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return []
    pois = result.get('pois', []) or result.get('results', [])
    items = []
    for p in pois:
        loc = p.get('location', '')
        if not loc or ',' not in str(loc):
            continue
        lng_str, lat_str = str(loc).split(',', 1)
        try:
            lng, lat = float(lng_str), float(lat_str)
        except ValueError:
            continue
        items.append(PoiInfo(name=p.get('name', ''), lng=lng, lat=lat, address=p.get('address', '')))
    return items

def extract_route(result: Any) -> list[list[float]] | None:
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return None
    route = (result.get('routes') or [{}])[0]
    path = route.get('path') or route.get('polyline', '')
    if not path:
        return None
    points = []
    for pair in str(path).split(';'):
        if ',' in pair:
            l, a = pair.split(',', 1)
            try:
                points.append([float(l), float(a)])
            except ValueError:
                continue
    return points if points else None

def extract_weather(result: Any) -> list[dict] | None:
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return None
    forecasts = result.get('forecasts', [])
    if not forecasts:
        return None
    return [{'date': f.get('date', ''), 'weather': f.get('dayweather', ''), 'temp': f.get('daytemp', '')} for f in forecasts]
```

### 4.8 Agent 服务层（`agent_service.py`）

```python
# ★ 意图检测 → planner 路由（替代当前 skill 触发路径）
# 当 planner 不可用时，Agent 通过 SkillToolset 走 travel-planner SKILL.md 降级

from agent.trip.planner import plan_trip

async def stream_ag_ui(self, request, user_id):
    ...
    user_message = extract_user_message(request)

    # 意图检测：旅行规划关键词
    if is_trip_intent(user_message):
        try:
            # ★ 主路径：planner 编排
            parsed = {
                'city': extract_city(user_message),
                'days': extract_days(user_message) or 3,
                'start_date': extract_date(user_message),
                'preferences': extract_preferences(user_message),
                'free_text': user_message,
                'message_history': await self._build_message_history(conv.id),
            }
            # 缺失信息 → 先让 Agent 追问（复用通用对话能力）
            if not parsed['city']:
                return await self.stream_ag_ui_normal(request, user_id)

            return StreamingResponse(
                _sse_wrap(plan_trip(agent=get_agent(), request=parsed)),
                media_type='text/event-stream',
            )
        except Exception:
            # ★ 降级路径：planner 挂了，Agent 自己走 skill
            logger.warning("planner failed, falling back to skill path")
            return await self.stream_ag_ui_normal(request, user_id)

    # 非旅行场景 → 现有通用对话
    return await self.stream_ag_ui_normal(request, user_id)
```

### 4.9 Agent 注册（`model_client.py` — 仅 +1 行）

```python
from agent.trip.hooks import trip_hooks

agent = Agent(model, toolsets=toolsets, capabilities=[trip_hooks])
```

### 4.10 前端（新增 Progress 事件处理）

```typescript
// stream-client.ts
if (type === 'MAP_DATA') {
  options.onMapData?.(data.payload)
}
if (type === 'PROGRESS') {
  options.onProgress?.({ phase: data.phase, percent: data.progress, message: data.message })
}

// useChatStream.ts
const progress = ref<{ phase: string; percent: number; message: string } | null>(null)
const mapMarkers = ref<MapMarker[]>([])
const mapPolylines = ref<MapPolyline[][]>([])

function handleMapData(data: MapData) {
  if (data.type === 'POI_FOUND' && data.markers) {
    mapMarkers.value.push(...data.markers)
  }
  if (data.type === 'ROUTE_SEGMENT' && data.polyline) {
    mapPolylines.value.push(data.polyline)
  }
}
```

---

## 5. 用户可见时间线

```
用户: "帮我规划北京三日游"
         │
[状态栏] 🔍 正在搜索景点和天气...          Phase 1, ~2s
[地图]   6 个 POI 标记出现 📍📍📍📍📍📍
[状态栏] 🚶 正在规划景点间路线...          Phase 2, ~2s
[地图]   5 条路线连线出现 ───
[状态栏] 📝 正在生成行程...               Phase 3, ~15s
[聊天]   "## 北京三日游行程规划\n\n        ← 流式逐字
          ## Day 1：天安门→故宫\n
          上午 8:00 — 参观..."
[地图]   餐饮 POI 陆续出现（随文本输出）
[完成]   行程生成完毕
```

---

## 6. SKILL.md（降级用，当 planner 不可用时 Agent 自己走）

```yaml
---
name: travel-planner
description: >
  [降级路径] 当 planner 编排器不可用时使用。
  多日旅游行程规划。触发词: "几日游""攻略""行程安排""去哪玩""旅游规划"。
auto_load_references:
  - references/itinerary-template.md
---

## 旅行规划工作流

### 第一步：确认信息
确认目的地、天数、日期。缺失的问一次，不答跳过。

### 第二步：收集数据
1. maps_text_search(keywords="<偏好>", city="<城市>") — 搜索景点
2. maps_weather(city="<城市>") — 查询天气

### 第三步：规划路线
对相邻景点调用 maps_direction_walking 或 maps_direction_transit_integrated。

### 第四步：生成行程
综合数据，按 `references/itinerary-template.md` 结构输出每日行程。
可调用 maps_around_search 搜索周边餐饮。
最后调用 maps_schema_personal_map 生成高德地图链接。

## 约束
- 景点信息必须来自工具返回，不得编造
- 每天 2-3 个景点，位置相邻
- 天气恶劣时给出室内备选
```

注意：SKILL.md 是**降级路径**——planner 是主路径。两者的输出格式对齐，前端无差别渲染。

---

## 7. 端到端数据流

```
用户: "帮我规划北京三日游，偏好历史文化"
         │
         ▼
┌─────────────────────────────────────┐
│ agent_service.stream_ag_ui()        │
│ → is_trip_intent() == True          │
│ → extract_city/days/preferences     │
│ → 所有信息齐全 → plan_trip()        │
│ → (缺少城市) → 通用对话追问          │
└─────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│ Planner: reset_map_bus()            │
│                                     │
│ Phase 1: agent.run()                │
│   prompt = PHASE1_PROMPT + 历史上下文│
│   LLM → TOOL_CALL maps_text_search  │
│        → hook → _map_bus.append(POI) │
│   LLM → TOOL_CALL maps_weather      │
│        → hook → _map_bus.append(WEA) │
│   → result.data = TripData(...)     │
│   → drain_map_events() → SSE        │
│   → ProgressEvent → SSE             │
│                                     │
│ Phase 2: agent.run()                │
│   (pois >= 2 时执行，否则跳过)        │
│   prompt = PHASE2_PROMPT + pois     │
│   LLM → TOOL_CALL maps_direction_*  │
│        → hook → _map_bus.append(RT)  │
│   → result.data = RouteData(...)    │
│   → drain_map_events() → SSE        │
│   → ProgressEvent → SSE             │
│                                     │
│ Phase 3: agent.run_stream()         │
│   prompt = PHASE3_PROMPT + 全量数据  │
│          + itinerary-template.md    │
│   TEXT: "## Day 1: 故宫→天坛\n"     │
│     → SSE: TEXT_MESSAGE_CONTENT     │
│   TOOL_CALL: maps_around_search     │
│     → hook → MapEvent 注入流        │
│   TEXT: "第2天..."                  │
│   RUN_FINISHED                      │
│   DoneEvent → SSE                   │
└─────────────────────────────────────┘
```

---

## 8. 实施清单

### 新建文件

| 文件 | 说明 |
|------|------|
| `src/agent/trip/__init__.py` | 模块入口 |
| `src/agent/trip/planner.py` | 三阶段编排器 |
| `src/agent/trip/prompts.py` | Phase 1/2/3 指令 |
| `src/agent/trip/schemas.py` | TripData, RouteData |
| `src/agent/trip/hooks.py` | Hooks + contextvars 事件总线 |
| `src/agent/trip/events.py` | ProgressEvent, MapEvent, DoneEvent |
| `src/agent/trip/extractors.py` | MCP 结果解析 |

### 修改文件

| 文件 | 变更 | 说明 |
|------|------|------|
| `src/agent/model_client.py` | +1 行 | `capabilities=[trip_hooks]` |
| `src/services/agent_service.py` | +30 行 | 意图检测 → planner 路由 + 降级 |
| `src/agent/skills/travel-planner/SKILL.md` | 重写 | 降级路径专用 |
| `web/src/api/stream-client.ts` | +15 行 | PROGRESS + MAP_DATA 事件 |
| `web/src/composables/useChatStream.ts` | +20 行 | progress + mapEvents 状态 |
| `web/src/components/TravelMap.vue` | 修改 | 增量渲染 |

### 不变文件

- `src/agent/mcp.py` — 不变
- `src/agent/skills/toolset.py` — 不变

---

## 9. 风险与对策

| 风险 | 对策 |
|------|------|
| MCP 返回格式不统一 | 实现 extractors 时实际调 MCP 验证返回结构，加 try/except 保护 |
| `result_type` 解析失败 | Phase 1/2 失败 → 降级到 SKILL.md 路径 |
| Phase 2 景点对顺序不合理 | PHASE2_PROMPT 明确"按列表顺序"，`RouteData.routes` 做 `min_length` 校验 |
| 意图检测误判 | 降低阈值 → 误判由 planner 的信息补全环节兜底；缺少城市则走通用对话追问 |
| 地图标记过多 | > 50 个标记时启用 AMap.MarkerClusterer 聚合 |
| Planner 异常 | catch 后降级到 SKILL.md 路径，用户无感 |

---

## 附录 A：架构决策记录（ADR）

### ADR-001：单 Agent 编排器 vs Planning Agent

**背景**：旅行规划涉及 3 个阶段（数据收集、路线规划、文案生成）。需要一个组件来编排这些阶段。

**方案 A：LLM 驱动的 Planning Agent**

新增一个 Agent，负责动态决定执行哪些阶段、调用哪些工具、如何处理中间结果。

- 优点：可处理非确定性工作流（如"先判断季节是否合适再规划"），Agent 可以根据中间结果自主调整策略
- 缺点：增加 1 次 LLM 调用（编排推理）、决策链不可预测（每次结果可能不同）、调试需要追踪 Agent 的 "思考过程"、token 消耗更高

**方案 B：纯函数编排器（选定）**

同一个通用 Agent 跑 3 次 `run()`，`planner.py` 是一个 60 行的 async 函数。

- 优点：确定性 100%（同样的输入 → 同样的执行路径）、零额外 token 消耗（不调 LLM 做编排）、阶段间用 Pydantic `result_type` 传递强类型数据、调试只需 print 3 条日志
- 缺点：工作流固定，无法动态调整。需要人工定义每阶段的 prompt 和工具

**决策**：选 **B**。

旅行规划 99% 的场景工作流是固定的（搜→算→写）。在确定性工作流上引入 Planning Agent 增加了延迟和不确定性，不带来任何用户可感知的价值。只有当需要动态决策时（如"先判断季节是否适合旅行，不适合则建议更换目的地"），A 的收益才开始出现。

**扩展点**：如果未来业务需要，`planner.py` 可升级为一个 Agent——不是替换现有 3 个阶段的 Agent，而是用 `result_type=WorkflowPlan` 让规划 Agent 输出一个执行计划，planner 再执行它。这是**正交的改动**，不影响现有 3 个阶段的 Agent 和 hooks。

---

### ADR-002：Hooks + contextvars vs Toolset 装饰器

**背景**：需要拦截高德 MCP 工具调用结果，提取坐标注入 SSE 流。

**方案 A**：自定义 `AbstractToolset` 装饰 amap MCPToolset

在 `get_tools()`、`call_tool()` 中拦截调用。需要手动管理 toolset 生命周期，修改 `model_client.py` 的 toolsets 组装逻辑。

**方案 B**：`Hooks` + `contextvars`（选定）

pydantic-ai 原生的 `@hooks.on.after_tool_execute(tools=[...])` 精确拦截 + `contextvars.ContextVar` 做跨 run 事件传递。

- 优点：零侵入 — 不修改 toolset 组装逻辑，只在 `capabilities=` 中加一行；`tools=[...]` 过滤器精确控制哪些工具被拦截；`defer_loading=True` 默认不激活
- 缺点：需要 `contextvars` 弥补 `ctx.state` 跨 run 生命周期不足的问题

**决策**：选 **B**。`Hooks` 是 pydantic-ai 为这个场景设计的原生机制，比手动写 Toolset 更少代码、更少 bug、更少维护负担。

---

### ADR-003：Planner 优先 vs Skill 优先

**背景**：项目已有 `SkillToolset` 机制，Agent 可以动态加载特定场景的 SKILL.md。Planner 引入了一个新的编排路径。

**方案 A**：让 Skill 系统完全处理旅行规划

Agent 通过 `SkillToolset` 检测旅行意图，加载 `travel-planner/SKILL.md`，在单次 `run()` 里执行全部 7-8 步工具调用。

- 优点：复用现有基础设施
- 缺点：长 run 的上下文膨胀、步骤遗漏、无法分阶段推送进度

**方案 B**：Planner 优先 + Skill 降级（选定）

`agent_service.py` 做意图检测 → planner 编排。SKILL.md 保留作为降级路径（planner 异常时）。两条路径的输出格式对齐，前端无差别渲染。

- 优点：主路径有分阶段编排的所有好处；降级路径保证可用性
- 缺点：需要维护两条路径的 prompt 一致性

**决策**：选 **B**。主路径和降级路径不是"重复"——主路径提供更好的体验和可靠性；降级路径保证 planner 挂了不影响可用性。两者共享相同的 MCP 工具和 hooks，只是编排方式不同。
