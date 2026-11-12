import json
import math
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    player: str = Field(pattern=r"^[A-Za-z0-9_]{3,16}$")
    message: str = Field(min_length=1, max_length=500)
    generation: int | None = Field(default=None, ge=0)

    @field_validator("message")
    @classmethod
    def meaningful_message(cls, value: str) -> str:
        if not value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("消息必须为非空单行文本。")
        return value.strip()


class EmptyArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Coordinates(EmptyArguments):
    x: float
    y: float
    z: float

    @field_validator("x", "y", "z", mode="before")
    @classmethod
    def valid_coordinate(cls, value: Any, info) -> float:
        bound = 2047 if info.field_name == "y" else 29999984
        minimum = -2048 if info.field_name == "y" else -bound
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not minimum <= value <= bound:
            raise ValueError("坐标超出范围或不是有限数字。")
        return float(value)


class IntentBase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reply: str = Field(min_length=1, max_length=500)

    @field_validator("reply")
    @classmethod
    def plain_reply(cls, value: str) -> str:
        if not value.strip() or "```" in value or any(ord(c) < 32 for c in value):
            raise ValueError("回复必须是普通单行文本。")
        return value.strip()


class SimpleIntent(IntentBase):
    intent: Literal["conversation", "query_status", "follow", "stop", "unsupported", "clarification"]
    arguments: EmptyArguments


class GotoIntent(IntentBase):
    intent: Literal["goto"]
    arguments: Coordinates


INTENT_ADAPTER = TypeAdapter(Annotated[SimpleIntent | GotoIntent, Field(discriminator="intent")])


def parse_intent(text: str) -> SimpleIntent | GotoIntent:
    # 只接受 JSON 数据；不提取代码围栏、不执行模型文本，也不丢弃多余字段。
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("重复 JSON 字段。")
            result[key] = value
        return result

    value = json.loads(text, object_pairs_hook=unique_object)
    allowed = {"conversation", "query_status", "follow", "goto", "stop", "unsupported", "clarification"}
    if isinstance(value, dict) and isinstance(value.get("intent"), str) and value["intent"] not in allowed:
        return SimpleIntent(intent="unsupported", arguments=EmptyArguments(), reply="这个操作目前尚不支持。")
    return INTENT_ADAPTER.validate_python(value)


def redact(text: str, key: str) -> str:
    if key:
        text = text.replace(key, "[已隐藏]")
    return re.sub(r"\bsk-[A-Za-z0-9_-]+", "[已隐藏]", text)
