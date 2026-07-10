"""Token-bounded PydanticAI history: cumulative summary plus recent complete turns."""

from __future__ import annotations

from collections import OrderedDict

from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)

from agent.model_client import get_compress_agent
from core.config import settings
from log import logger
from models.conversation import Conversation
from models.conversation_message import ConversationMessage
from repositories import ConversationMessageRepository, ConversationRepository

SUMMARY_INSTRUCTIONS = """将旧对话压缩为后续对话需要的上下文。
保留用户事实与偏好、已确认决定、当前目标、重要约束、未完成事项和重要工具结果。
删除寒暄、重复内容、工具协议细节和已被后续内容推翻的信息。
冲突时以较新的事实为准，不得添加不存在的信息。
输出简洁中文，不超过指定长度。"""


class HistoryManager:
    def __init__(
        self,
        message_repo: ConversationMessageRepository,
        conversation_repo: ConversationRepository,
    ):
        self.message_repo = message_repo
        self.conversation_repo = conversation_repo

    @staticmethod
    def _group_turns(
        rows: list[ConversationMessage],
    ) -> list[list[ConversationMessage]]:
        groups: OrderedDict[str, list[ConversationMessage]] = OrderedDict()
        for row in rows:
            groups.setdefault(row.turn_id, []).append(row)
        return list(groups.values())

    @staticmethod
    def _deserialize(rows: list[ConversationMessage]) -> list[ModelMessage]:
        data = [row.message_data for row in rows if row.message_data is not None]
        return ModelMessagesTypeAdapter.validate_python(data) if data else []

    @staticmethod
    def _estimate_tokens(rows: list[ConversationMessage]) -> int:
        total_chars = sum(len(row.content or "") for row in rows)
        return max(1, total_chars // 3)

    @staticmethod
    def _project_turns(turns: list[list[ConversationMessage]]) -> str:
        lines: list[str] = []
        for turn in turns:
            for row in turn:
                if row.message_data is None:
                    continue
                message = ModelMessagesTypeAdapter.validate_python([row.message_data])[
                    0
                ]
                if isinstance(message, ModelRequest):
                    for part in message.parts:
                        if isinstance(part, UserPromptPart) and isinstance(
                            part.content, str
                        ):
                            lines.append(f"用户：{part.content}")
                        elif isinstance(part, ToolReturnPart):
                            value = str(part.content)
                            lines.append(f"重要工具结果：{value[:1500]}")
                elif isinstance(message, ModelResponse):
                    text = "".join(
                        part.content
                        for part in message.parts
                        if isinstance(part, TextPart)
                    )
                    if text:
                        lines.append(f"助手：{text}")
        return "\n".join(lines)

    async def _compact(
        self,
        conversation: Conversation,
        turns: list[list[ConversationMessage]],
    ) -> bool:
        keep = settings.HISTORY_KEEP_RECENT_TURNS
        old_turns = turns[:-keep]
        if not old_turns:
            return False
        checkpoint = old_turns[-1][-1]
        projection = self._project_turns(old_turns)
        prompt = (
            f"旧摘要：\n{conversation.summary or '无'}\n\n"
            f"新增旧对话：\n{projection}\n\n"
            f"最终摘要最多 {settings.HISTORY_MAX_SUMMARY_CHARS} 个字符。"
        )
        result = await get_compress_agent().run(
            prompt, instructions=SUMMARY_INSTRUCTIONS
        )
        summary = str(result.output).strip()
        if not summary:
            raise RuntimeError("compression model returned an empty summary")
        summary = summary[: settings.HISTORY_MAX_SUMMARY_CHARS]
        return await self.conversation_repo.compare_and_set_summary(
            conversation,
            expected_message_id=conversation.summary_until_message_id,
            summary=summary,
            until_message_id=checkpoint.id,
            until_created_at=checkpoint.created_at,
        )

    async def build(
        self,
        *,
        conversation: Conversation,
        before_message_id: int,
    ) -> list[ModelMessage]:
        rows = await self.message_repo.list_native_history(
            conversation_id=conversation.id,
            after_id=conversation.summary_until_message_id,
            before_id=before_message_id,
        )
        turns = self._group_turns(rows)
        should_compact = (
            len(turns) >= settings.HISTORY_SUMMARY_TRIGGER_TURNS
            or self._estimate_tokens(rows) >= settings.HISTORY_SUMMARY_TRIGGER_TOKENS
        )
        if should_compact:
            try:
                changed = await self._compact(conversation, turns)
                if changed:
                    refreshed = await self.conversation_repo.get_by_id(conversation.id)
                    if refreshed is not None:
                        conversation = refreshed
                    rows = await self.message_repo.list_native_history(
                        conversation_id=conversation.id,
                        after_id=conversation.summary_until_message_id,
                        before_id=before_message_id,
                    )
                    turns = self._group_turns(rows)
            except Exception:
                logger.exception(
                    "history compaction failed; using recent complete turns"
                )
                turns = turns[-settings.HISTORY_KEEP_RECENT_TURNS :]

        history: list[ModelMessage] = []
        if conversation.summary:
            history.append(
                ModelRequest(
                    parts=[
                        SystemPromptPart(
                            content=f"较早对话摘要，仅作为历史上下文：\n{conversation.summary}"
                        )
                    ]
                )
            )
        for turn in turns:
            history.extend(self._deserialize(turn))
        return history


def serialize_native_messages(
    messages: list[ModelMessage],
) -> list[tuple[str, str, dict]]:
    """Convert native messages into atomic DB rows without losing provider metadata."""
    data = ModelMessagesTypeAdapter.dump_python(messages, mode="json")
    result: list[tuple[str, str, dict]] = []
    for message, payload in zip(messages, data, strict=True):
        role = "assistant"
        content = ""
        if isinstance(message, ModelRequest):
            role = "tool"
            for part in message.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    role, content = "user", part.content
                    break
                if isinstance(part, ToolReturnPart):
                    content = str(part.content)
        elif isinstance(message, ModelResponse):
            content = "".join(
                part.content for part in message.parts if isinstance(part, TextPart)
            )
        result.append((role, content, payload))
    return result
