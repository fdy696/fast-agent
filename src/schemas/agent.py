from pydantic import BaseModel, field_validator


def _check_not_empty(v, info):
    if not v or not v.strip():
        raise ValueError(f"{info.field_name} 不能为空")
    return v


class ChatRequest(BaseModel):
    sessionId: str
    content: str

    @field_validator("sessionId", "content")
    @classmethod
    def check_not_empty(cls, v, info):
        return _check_not_empty(v, info)


class GetConversationRequest(BaseModel):
    session_id: str

    @field_validator("session_id")
    @classmethod
    def check_not_empty(cls, v, info):
        return _check_not_empty(v, info)


class GetTravelPlanRequest(BaseModel):
    plan_id: str
    session_id: str

    @field_validator("plan_id", "session_id")
    @classmethod
    def check_not_empty(cls, v, info):
        return _check_not_empty(v, info)
