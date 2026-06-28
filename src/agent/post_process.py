import json
from typing import Any, Iterable, List, cast

from openai.types.chat import ChatCompletion, ChatCompletionMessageParam

from agent.model_prompt import COMPRESS_CHAT_PROMPT, POST_JSON_PROMPT, TITLE_PROMPT
from agent.model_client import call_compress_model, call_model
from db.session import AsyncSessionLocal
from log import logger
from models.travel_plan import TravelPlan


# 仅对首条用户消息做标题，过长才调模型；阈值（字符数）
TITLE_SUMMARY_THRESHOLD = 20
TITLE_MAX = 30


def _coerce_title(raw: str) -> str:
    """把首条用户消息规整为标题候选：去首尾空白、去换行折叠空白、截断。"""
    cleaned = " ".join(raw.split())
    return cleaned[:TITLE_MAX]


async def generate_conversation_title(first_user_msg: str) -> str:
    """根据首条用户消息生成会话标题。

    - 短消息(<=TITLE_SUMMARY_THRESHOLD)：直接清洗截断当标题，零 LLM 调用。
    - 长消息：仅用该首条消息 + TITLE_PROMPT 调一次轻量模型提炼，绝不混入
      系统提示词、历史消息或工具结果。
    失败回退到清洗截断。
    """
    cleaned = _coerce_title(first_user_msg)
    if len(cleaned) <= TITLE_SUMMARY_THRESHOLD:
        return cleaned
    try:
        res = await call_compress_model(
            messages=[{"role": "system", "content": TITLE_PROMPT + first_user_msg}]
        )
        title = _coerce_title(res.choices[0].message.content or "")
        return title or cleaned
    except Exception as e:
        logger.warning(f"title generation failed, fallback to truncation: {e}")
        return cleaned


async def deepseek_compress_chat(raw_markdown: str) -> str:
    """用 DeepSeek 将行程文本压缩为 150 字以内的摘要"""
    prompt = f"{COMPRESS_CHAT_PROMPT}\n内容如下:\n{raw_markdown}"
    try:
        res = await call_compress_model(
            messages=[{"role": "system", "content": prompt}]
        )
        compressed_content = res.choices[0].message.content
        return compressed_content
    except Exception as e:
        logger.error(f"chat compression failed: {e}")
        return raw_markdown[:150] + "..."


async def generate_structured_itinerary(
    agent_context: List[dict[str, Any]],
) -> Any:
    """调用 LLM 将对话上下文提取为结构化行程 JSON"""
    structured_context: List[dict[str, Any]] = [
        {"role": "system", "content": POST_JSON_PROMPT}
    ] + agent_context[1:]

    response: ChatCompletion = await call_model(
        messages=cast(Iterable[ChatCompletionMessageParam], structured_context),
        tools=[],
        response_format="json_object",
        stream=False,
        tool_choice="none",
    )
    try:
        json_str = response.choices[0].message.content or "{}"
        result_data = json.loads(json_str)
        return result_data
    except Exception as e:
        logger.error(f"structured JSON parse failed: {e}")
        return None


async def safe_extract_itinerary(
    agent_context: List[dict[str, Any]],
    current_plan_id: str,
    session_id: str,
):
    """异步行程 JSON 提取 + 写入 TravelPlan 表（纯后台，不碰 queue）。

    成功：写 status=1 + plan_data；失败：写 status=0 + plan_data={"error":...}，
    让前端走 REST 拉取时能区分「生成中(404)」与「生成失败」。
    """
    try:
        final_itinerary_json = await generate_structured_itinerary(agent_context)
        if not final_itinerary_json:
            raise Exception("JSON 提取结果为空")
        async with AsyncSessionLocal() as sub_session:
            new_plan = TravelPlan(
                plan_id=current_plan_id,
                session_id=session_id,
                plan_data=final_itinerary_json,
                status=1,
            )
            sub_session.add(new_plan)
            await sub_session.commit()
    except Exception as e:
        import traceback
        logger.error(f"itinerary extraction failed: {e}")
        logger.error(traceback.format_exc())
        async with AsyncSessionLocal() as sub_session:
            sub_session.add(TravelPlan(
                plan_id=current_plan_id,
                session_id=session_id,
                plan_data={"error": "行程清单生成失败,可重试"},
                status=0,
            ))
            await sub_session.commit()
