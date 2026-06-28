from __future__ import annotations
from datetime import datetime, timezone, timedelta

BASE_PROMPT = """
你是用户的智能助理。
- 当前日期: {current_date}（仅供你了解"今天几号"，不是用户的出行日期。出行日期只能由用户明确告知，禁止用今天推算）
- 有工具就用工具，没工具就直接回答。
- 禁止编造任何天气数据、图片 URL、地理坐标。
- 不知道就说不知道。
- 可用能力：调用 load_skill 按需获得能力的完整工作流。当前可用能力：{skills}

{user_preference}
"""

_CST = timezone(timedelta(hours=8))
_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# 结构化偏好 → 人类可读展示 + plan_itinerary 参数映射
# 每个字段：(显示名, 参数名或 None, 示例, LLM 提取规则)
_PREFERENCE_LABELS: dict[str, tuple[str, str | None, str, str]] = {
    'budget': (
        '预算档位', 'budget',
        '经济型、舒适型、中高端、豪华型',
        '提取时把具体金额（如人均500、总预算2000）归结为档位，不要存具体金额',
    ),
    'travel_style': (
        '旅行风格', 'preferences',
        '自然山水、人文历史、休闲度假、美食探店',
        '',
    ),
    'dietary_restrictions': (
        '饮食限制', None,
        '不吃辣、清真、素食、海鲜过敏',
        '',
    ),
    'transport': (
        '交通偏好', 'transport',
        '高铁、自驾、飞机',
        '',
    ),
    'accommodation': (
        '住宿偏好', 'accommodation',
        '市中心便利、观景民宿、经济连锁、高端度假酒店',
        '',
    ),
    'travel_companion': (
        '同行人', 'travelers',
        '情侣、家庭、独自、朋友结伴',
        '',
    ),
    'visited_destinations': (
        '已去过', None,
        '天津、云南',
        '',
    ),
}

# 从 _PREFERENCE_LABELS 自动生成 LLM 提取提示（MEMORY_PROMPT 用）
def build_memory_field_hints() -> str:
    lines: list[str] = []
    for key, (label, _, examples, rule) in _PREFERENCE_LABELS.items():
        hint = f"- {key} (string|null)：{label}"
        if examples:
            hint += '，如"' + examples + '"'
        if rule:
            hint += f"。{rule}"
        else:
            hint += "。"
        lines.append(hint)
    return "\n".join(lines)


def _format_preference(prefs: dict | None) -> str:
    """将 DB 中的偏好 dict 格式化为可注入 System Prompt 的文本。"""
    if not prefs or not isinstance(prefs, dict):
        return ""

    lines: list[str] = ['# 用户历史偏好（已存字段直接采用，只追问缺失项；用户本轮明确说了相反内容则以本轮为准）']
    param_hints: list[str] = []

    for key, (label, param, *_rest) in _PREFERENCE_LABELS.items():
        value = prefs.get(key)
        if value is None:
            continue
        display = ', '.join(value) if isinstance(value, list) else str(value)
        if not display.strip():
            continue
        lines.append(f"- {label}: {display}")
        if param:
            param_hints.append(f"{label}→{param}")

    if param_hints:
        lines.append(f"已有值：{'、'.join(param_hints)}，有值就传，不跳过。")

    return "\n".join(lines) if len(lines) > 1 else ""


def build_system_prompt(user_preference: dict | None = None) -> str:
    """每次请求时调用，注入当前日期 + 技能清单 + 用户长期偏好到 System Prompt"""
    from agent.skills.registry import registry

    formatted_pref = _format_preference(user_preference)
    now = datetime.now(_CST)
    return BASE_PROMPT.format(
        current_date=now.strftime(f"%Y年%m月%d日 {_WEEKDAYS[now.weekday()]}"),
        skills=registry.build_skill_metadata(),
        user_preference=formatted_pref or "# 用户偏好与历史\n暂无",
    )

POST_JSON_PROMPT = """
你是一个高级旅游数据结构化专家。

你的唯一任务：
根据上下文对话中，Assistant 刚刚生成的最终行程规划（最后一轮对话），以及之前的工具（role: tool）返回的原始数据，将其转化为前端渲染所需的 JSON 格式。
【关键要求】
1. **数据提取**：从上下文的行程描述里提取天数、时段、景点名称及描述。
2. **id生成**: itinerary_list里的id字段使用随机数生成，数字加英文，不能出现重复。
3. **多媒体关联**：将 `search-image` 工具返回的图片链接，精准匹配到对应的景点 `toponym_image_list` 中。
4. **坐标补全**：
   - 必须通过上下文中的 `role: tool` (地图工具结果) 提取对应的每个景点的经纬度。
   - 如果上下文缺失坐标经纬度，请根据景点名称使用你内部数据进行推测，精确到小数点后6位，比如：26.954260,100.216991。
5. **严格字段映射**：
   - `title`: 在文字前面加一个对应的icon小图标。
   - `toponym_title`: 这个字段不改变，值就是"核心体验"。
   - `toponym_image_title`: 这个字段不改变，值就是"景点图片"。
   - `toponym_desc`: 核心体验描述，字数控制在 150 字以内。在文字前面加一个对应的icon小图标
   - `toponym_image`: 封面图从对应的景点工具里随机取一张图片。
   - `toponym_image_list`: 工具里有多少张图片就取多少张图片


【输出 JSON 格式】
{{
  "title": "行程主标题",
  "itinerary_list": [
    {{
      "id": "fhgut789der",
      "day": "第一天",
      "itinerary": [
        {{
          "time": "上午",
          "scenic_spots_list": [
            {{
              "toponym": "景点名称",
              "toponym_image": "封面图URL",
              "toponym_title": "核心体验",
              "toponym_desc": "描述文案",
              "toponym_image_title": "景点图片",
              "toponym_image_list": ["url1", "url2"],
              "longitude": 116.40,
              "latitude": 39.90
            }}
          ]
        }}
        //下午，晚上
      ]
    }}
    //第二天，第三天
  ]
}}
【严格约束】
只能返回json数据，不能返回其他和说明
"""

COMPRESS_CHAT_PROMPT = """
请将下方的旅游行程规划压缩为一段简短的纯文本摘要（500字以内）。
要求：
1. 保留目的地、天数、核心景点列表、天气概况、美食推荐要点。
2. 剔除所有 Markdown 表格、图片链接、详细描述和排版符号。
3. 保留后续可能需要追问的关键信息（具体景点名、推荐菜名、地址、门票参考价）。
内容如下：
"""

TITLE_PROMPT = """将下方用户首条消息提炼为一段不超过 12 个字的简短会话标题，
用于会话列表展示。要求：
1. 只基于用户消息本身，不要回答问题、不要带入系统提示或历史上下文。
2. 指向用户意图（如"规划北京三日游"、"查北京天气"），不要带"标题:"等前缀。
3. 若消息已是短语，可直接精简；若过长则概括核心意图。
只返回标题文本。
"""
