"""
Agent API DTO — Chat 请求/响应。
"""

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    message: str = Field(
        min_length=1,
        validation_alias=AliasChoices("message", "content"),
    )
    session_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("session_id", "sessionId"),
    )
    system_prompt: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    answer: str
    session_id: str | None = None
    model: str | None = None
    tool_calls: list[dict[str, object]] = Field(default_factory=list)
