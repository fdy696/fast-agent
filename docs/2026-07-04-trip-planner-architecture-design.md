# 旅行规划场景架构设计

> 日期: 2026-07-04 | 状态: 待评审 | 版本: 2.0 (基于 Hooks)

## 1. 设计目标

在通用聊天 Agent 架构之上，以**零耦合**方式接入旅行规划场景，实现：

1. Agent 自动调用高德 MCP 工具（POI 搜索、路径规划、天气查询）
2. 工具调用结果中的结构化坐标数据**流式推送**前端，增量渲染多日行程地图
3. 旅行规划能力作为独立模块，与通用对话、其他场景 Skill 互不干扰
4. 未来新增场景（餐饮推荐、酒店预订）可复用相同模式

---

## 2. 核心设计原则

### 2.1 解耦策略：Hooks + defer_loading

pydantic-ai 的 `Hooks` 机制提供了完整的 Agent 生命周期钩子（见 [pydantic-ai hooks 文档](https://pydantic.dev/docs/ai/core-concepts/hooks/)）：

```
Agent 生命周期:

  before_run ──▶ model_request ──▶ ... ──▶ after_run
                    │
          ┌────────┼────────┐
          ▼        ▼        ▼
    tool_validate  tool_execute  event (stream)
          │              │           │
    before/after    before/after   each SSE event
```

**关键设计**：

- **`defer_loading=True`**：旅行 hooks 默认不激活。只有当 skill `travel-planner` 被触发时，Agent 调用 `load_capability('trip-planner')` 后才加载，零开销
- **`tools=[...]` 过滤**：精确指定拦截哪些 MCP 工具，不相关的工具调用不受影响
- **`ctx.state` 传递**：hooks 间通过 `RunContext.state` 共享 MapEvent 队列，不污染全局状态

### 2.2 数据源：MCP 工具返回值就是结构化坐标

高德 MCP 的 15 个工具每次调用都返回结构化数据：

| 工具 | 返回值 | 提取字段 |
|------|--------|---------|
| `maps_text_search` | `{ pois: [{ name, location, address }] }` | markers |
| `maps_around_search` | 同上 | markers |
| `maps_direction_*` | `{ routes: [{ path: "lng,lat;lng,lat;..." }] }` | polyline |
| `maps_weather` | `{ forecasts: [{ date, daytemp, nighttemp, dayweather }] }` | weather |

**不需要解析 LLM 输出文本。** 工具调用就是天然的结构化数据事件源。

### 2.3 事件流：拦截 Agent 流事件，注入自定义 SSE 事件

```
LLM 输出:      TEXT delta 流式                → 聊天文本（不变）
MCP 工具调用:   TOOL_CALL_START → TOOL_CALL_RESULT
                                              ↓
                              after_tool_execute hook 触发
                              提取坐标 → ctx.state['trip_map_events'].append()
                                              ↓
                              event hook 拦截流事件
                              在正常事件间插入 MAP_DATA 事件
                                              ↓
自定义 SSE:     MAP_DATA(POI_FOUND/ROUTE_SEGMENT) → 前端地图增量渲染
```

AG-UI 协议不限制事件类型——可以在标准事件（TEXT/RUN_FINISHED/ERROR）之间注入任意自定义事件。

---

## 3. 模块结构

```
src/agent/
├── skills/
│   └── travel-planner/            # 现有 Skill（SKILL.md 重写）
│       ├── SKILL.md
│       └── references/
│           └── itinerary-template.md
│
├── trip/                          # ★ 新增：旅行规划 hooks 模块
│   ├── __init__.py
│   ├── hooks.py                   # Hooks 注册（tool_execute + event 钩子）
│   ├── events.py                  # MapEvent 模型
│   └── extractors.py             # 工具结果解析（poi/route/weather）
│
├── mcp.py                         # 现有 MCP 管理（不变）
├── model_client.py                # ★ 加一行：capabilities=[trip_hooks]
└── skills/                        # 现有 SkillToolset（不变）

src/
├── services/
│   └── agent_service.py           # 不变

web/src/
├── api/
│   └── stream-client.ts           # ★ 解析 MAP_DATA 事件
├── composables/
│   └── useChatStream.ts           # ★ 暴露 mapEvents 状态
├── components/
│   └── TravelMap.vue              # ★ 支持增量 render
```

---

## 4. 核心组件设计

### 4.1 Hooks 模块（`src/agent/trip/hooks.py`）

```python
"""旅行规划 hooks — 拦截高德 MCP 工具调用，提取结构化坐标注入 SSE 流。"""

from pydantic_ai.capabilities import Hooks
from pydantic_ai.tools import RunContext
from pydantic_ai.agent import AgentStreamEvent
from .events import MapEvent
from .extractors import extract_pois, extract_route, extract_weather

trip_hooks = Hooks(
    id='trip-planner',
    description='旅行规划能力：拦截高德 MCP 工具调用结果，提取景点坐标和路线、天气信息，推流前端渲染行程地图',
    defer_loading=True,
)

# ── POI 搜索拦截 ──────────────────────────────────────────

@trip_hooks.on.after_tool_execute(
    tools=['maps_text_search', 'maps_around_search', 'maps_search_detail']
)
async def on_poi_found(ctx: RunContext, *, call, tool_def, args, result):
    """地图搜索完成 → 提取 POI 坐标，累积到 ctx.state。"""
    pois = extract_pois(result)
    if not pois:
        return
    events = ctx.state.setdefault('trip_map_events', [])
    events.append(MapEvent(
        type='POI_FOUND',
        markers=[{
            'name': p.name,
            'lng': p.lng,
            'lat': p.lat,
            'address': p.address,
        } for p in pois],
    ))

# ── 路线规划拦截 ───────────────────────────────────────────

@trip_hooks.on.after_tool_execute(
    tools=['maps_direction_walking', 'maps_direction_driving',
           'maps_direction_transit_integrated', 'maps_direction_bicycling']
)
async def on_route_planned(ctx: RunContext, *, call, tool_def, args, result):
    """路线规划完成 → 提取 polyline，累积到 ctx.state。"""
    polyline = extract_route(result)
    if not polyline:
        return
    events = ctx.state.setdefault('trip_map_events', [])
    events.append(MapEvent(
        type='ROUTE_SEGMENT',
        polyline=[{'lng': p[0], 'lat': p[1]} for p in polyline],
        from_addr=args.get('origin', ''),
        to_addr=args.get('destination', ''),
    ))

# ── 天气查询拦截 ───────────────────────────────────────────

@trip_hooks.on.after_tool_execute(tools=['maps_weather'])
async def on_weather(ctx: RunContext, *, call, tool_def, args, result):
    """天气查询完成 → 提取预报数据。"""
    forecasts = extract_weather(result)
    if not forecasts:
        return
    events = ctx.state.setdefault('trip_map_events', [])
    events.append(MapEvent(type='WEATHER_UPDATE', forecasts=forecasts))

# ── 流事件注入 ─────────────────────────────────────────────

@trip_hooks.on.run_event_stream
async def inject_map_events(ctx: RunContext, *, stream):
    """在每个标准事件前，将累积的 MAP_DATA 事件插入流中。"""
    async for event in stream:
        events = ctx.state.get('trip_map_events', [])
        while events:
            yield events.pop(0)  # 先注入累积的自定义事件
        yield event               # 再透传原始事件
```

### 4.2 提取器（`src/agent/trip/extractors.py`）

```python
"""高德 MCP 工具结果解析 —— 每个工具返回值格式不同，各自处理。"""

from dataclasses import dataclass
from typing import Any

@dataclass
class PoiInfo:
    name: str
    lng: float
    lat: float
    address: str = ""

def extract_pois(result: Any) -> list[PoiInfo]:
    """从 maps_text_search / maps_around_search / maps_search_detail 结果中提取 POI。"""
    if isinstance(result, str):
        import json
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return []
    pois = result.get('pois', []) or result.get('results', [])
    return [
        PoiInfo(
            name=p.get('name', ''),
            lng=float(loc.split(',')[0]) if (loc := p.get('location', '')) else 0,
            lat=float(loc.split(',')[1]) if (loc := p.get('location', '')) else 0,
            address=p.get('address', ''),
        )
        for p in pois
        if p.get('location')
    ]

def extract_route(result: Any) -> list[tuple[float, float]] | None:
    """从 maps_direction_* 结果中提取 polyline 路径。"""
    if isinstance(result, str):
        import json
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return None
    route = (result.get('routes') or [{}])[0]
    path = route.get('path') or route.get('polyline', '')
    if not path:
        return None
    return [
        (float(lng), float(lat))
        for pair in path.split(';')
        if ',' in pair
        for lng, lat in [pair.split(',')]
    ]

def extract_weather(result: Any) -> list[dict] | None:
    """从 maps_weather 结果中提取天气预报。"""
    if isinstance(result, str):
        import json
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return None
    forecasts = result.get('forecasts', [])
    if not forecasts:
        return None
    return [
        {
            'date': f.get('date', ''),
            'weather': f.get('dayweather', ''),
            'temp': f.get('daytemp', ''),
        }
        for f in forecasts
    ]
```

### 4.3 模型事件（`src/agent/trip/events.py`）

```python
from dataclasses import dataclass, field

@dataclass
class MapEvent:
    type: str  # 'POI_FOUND' | 'ROUTE_SEGMENT' | 'WEATHER_UPDATE'
    markers: list[dict] = field(default_factory=list)
    polyline: list[dict] = field(default_factory=list)
    forecasts: list[dict] = field(default_factory=list)
    from_addr: str = ""
    to_addr: str = ""

    def to_sse(self) -> str:
        import json
        return f"data: {json.dumps({'type': 'MAP_DATA', 'payload': self.__dict__}, ensure_ascii=False)}\n\n"
```

### 4.4 Agent 注册（`src/agent/model_client.py` — 仅加一行）

```python
from agent.trip.hooks import trip_hooks

def init_agent(registry=None) -> Agent:
    ...
    agent = Agent(
        model,
        toolsets=toolsets,
        capabilities=[trip_hooks],  # ★ 仅此一行
    )
```

### 4.5 前端 SSE 事件解析（`stream-client.ts`）

```typescript
// 新增接口
interface MapMarker {
  name: string
  lng: number
  lat: number
  address?: string
}

interface MapPolyline {
  lng: number
  lat: number
}

interface MapData {
  type: 'POI_FOUND' | 'ROUTE_SEGMENT' | 'WEATHER_UPDATE'
  markers?: MapMarker[]
  polyline?: MapPolyline[]
  from_addr?: string
  to_addr?: string
  forecasts?: Array<{ date: string; weather: string; temp: string }>
}

interface StreamClientOptions {
  // ... 现有选项 ...
  onMapData?: (data: MapData) => void   // ★ 新增
}

// SSE 解析
if (type === 'MAP_DATA') {
  options.onMapData?.(data.payload as MapData)
}
```

### 4.6 前端增量渲染

```typescript
// useChatStream.ts
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

```vue
<!-- TravelMap.vue -->
<script setup>
const props = defineProps<{
  markers: MapMarker[]
  polylines: MapPolyline[][]
}>()

// 增量添加新标记，不重绘已有标记
watch(
  () => props.markers.length,
  (newLen, oldLen) => {
    const added = props.markers.slice(oldLen || 0)
    for (const m of added) {
      mapInstance?.add(new AMap.Marker({ position: [m.lng, m.lat], title: m.name }))
    }
    mapInstance?.setFitView()
  }
)
</script>
```

---

## 5. 端到端数据流

```
用户: "帮我规划北京三日游"
         │
         ▼
┌────────────────────────────────┐
│ Agent 判断 → load skill         │
│ "travel-planner"                │
│ → load_capability("trip-planner")│  ← 激活 trip_hooks
│ → SKILL.md 指令注入 system prompt│
└────────────────────────────────┘
         │
         ▼  Agent.run_stream() ──▶ SSE event stream
         │
    ┌────┴────┐
    │ LLM 轮次 │
    └────┬────┘
         │ TOOL_CALL: maps_text_search("故宫", "北京")
         ▼
    ┌──────────────┐
    │ MCP 返回       │  { pois: [{ name:"故宫", location:"116.397,39.916" }] }
    └──────┬───────┘
           │
           ├──▶ TOOL_CALL_RESULT → AG-UI 标准事件（不变）
           │
           └──▶ after_tool_execute hook 自动触发
                    │
                    │ extract_pois(result)
                    │ ctx.state['trip_map_events'].append(...)
                    │
           ┌────────┘
           │ 下一个 event hook 触发
           │
           ├──▶ drain ctx.state['trip_map_events']
           │    → MapEvent(type="POI_FOUND", markers=[...])
           │
           ├──▶ SSE: {"type":"MAP_DATA","payload":{...}}
           │
           └──▶ TEXT_MESSAGE_CONTENT.delta → 聊天文本（正常流）
                    │
                    ▼
         ┌──────────────────────────────┐
         │ 前端 stream-client.ts          │
         │ onMapData(event)               │
         │ → mapMarkers.push(...markers)  │
         └──────────────────────────────┘
                    │
                    ▼
         ┌──────────────────────────────┐
         │ TravelMap.vue                  │
         │ watch markers.length           │
         │ → map.add(new Marker(...))    │
         │ → 地图出现故宫📍               │
         └──────────────────────────────┘

    ... 重复（长城📍、天坛📍、天气🌤、路线 ─）...

    ┌──────────────────────────────┐
    │ RUN_FINISHED                  │
    │ → SSR: {"type":"RUN_FINISHED",...}
    │ → 前端展示完整行程地图          │
    └──────────────────────────────┘
```

---

## 6. SKILL.md 重写

```yaml
---
name: travel-planner
description: >
  多日旅游行程规划与攻略。当用户提及"几日游""攻略""行程安排""去哪玩""旅游规划"等内容时，
  必须先调用 load_skill("travel-planner") 获取完整工作流，再按其中步骤行动。
  纯闲聊、查天气、查门票、查单个景点信息不适用。
auto_load_references:
  - references/itinerary-template.md
---

## 旅行规划工作流

### 第一步：确认信息

确认目的地、天数、日期、出发地。已有画像（偏好/预算）直接采用，缺失的问一次，不答跳过。

### 第二步：收集数据

确认信息齐全后，**按顺序**调用以下高德 MCP 工具。不要并行 — 先用 text_search 搜景点，拿到坐标后再规划路线。

1. `maps_text_search(keywords="<偏好>", city="<城市>")` — 搜索景点（坐标、地址）
2. 对每个景点，如需详细信息：`maps_search_detail(id="<poi_id>")`
3. `maps_weather(city="<城市>")` — 查询天气
4. 对相邻景点两两之间：`maps_direction_walking(origin="lng,lat", destination="lng,lat")` 或 `maps_direction_transit_integrated(...)` — 规划通勤路线
5. 搜索景点周边餐饮：`maps_around_search(keywords="餐饮", location="lng,lat", radius=1000)`

### 第三步：生成行程

综合所有工具返回的信息，严格按 `references/itinerary-template.md` 结构输出每日行程。

### 关键约束
- 景点名称、地址、坐标必须来自工具返回结果，不得编造
- 每天 2-3 个景点，按地理位置相邻原则分组
- 天气恶劣时给出室内备选方案
- 人群适配：婴幼儿→推车友好、老人→避免爬山/长步行
- 输出完行程后，调用 maps_schema_personal_map 生成高德地图行程链接
```

---

## 7. 解耦验证

| 维度 | 验证方式 |
|------|---------|
| **Agent 无感知** | `capabilities=[trip_hooks]` 一行注册，Agent 类无需修改 |
| **按需激活** | `defer_loading=True`，非旅行场景下 hooks 零开销 |
| **事件通道隔离** | `@hooks.on.event` 在标准 AG-UI 事件流中插入 MAP_DATA，前端按需监听 |
| **状态隔离** | 所有事件通过 `ctx.state` 传递，per-run 隔离，不同用户互不影响 |
| **新增场景零侵入** | 新增"餐饮推荐"场景：`src/agent/restaurant/hooks.py` + `skills/restaurant/SKILL.md`，其余代码不变 |
| **测试友好** | 每个 hook 是纯异步函数，传入 mock ctx + result，断言 ctx.state 内容 |

---

## 8. 实施清单

### 新建文件

| 文件 | 说明 |
|------|------|
| `src/agent/trip/__init__.py` | 模块入口，导出 `trip_hooks` |
| `src/agent/trip/hooks.py` | Hooks 注册（after_tool_execute + event） |
| `src/agent/trip/extractors.py` | 高德 MCP 返回值解析（POI/Route/Weather） |
| `src/agent/trip/events.py` | `MapEvent` 数据类 + SSE 序列化 |

### 修改文件

| 文件 | 变更 | 说明 |
|------|------|------|
| `src/agent/model_client.py` | +1 行 | `capabilities=[trip_hooks]` |
| `src/agent/skills/travel-planner/SKILL.md` | 重写 | 使用高德 MCP 工具替代 web_search |
| `web/src/api/stream-client.ts` | +10 行 | 解析 MAP_DATA 事件 |
| `web/src/composables/useChatStream.ts` | +15 行 | mapEvents 状态管理 |
| `web/src/components/TravelMap.vue` | 修改 | 增量渲染模式 |

### 不变文件

- `src/services/agent_service.py` — **零改动**
- `src/agent/mcp.py` — 不变
- `src/agent/skills/toolset.py` — 不变

---

## 9. 风险与对策

| 风险 | 对策 |
|------|------|
| MCP 返回格式不统一 | 先写 extractors 时实际调 MCP 验证每个工具的返回结构，加 fallback 处理 |
| LLM 不按顺序调工具 | SKILL.md 明确写"按顺序"，hooks 不依赖调用顺序 |
| `ctx.state` 跨 hook 丢失 | pydantic-ai 保证同一 run 内 ctx 是同一个 RunContext 实例 |
| 前端地图标记过多 | 超过 50 个标记启用 AMap.MarkerClusterer 聚合 |
