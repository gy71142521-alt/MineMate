"""Durable memory vocabulary.

The session event log is the single source of truth. LLM message history is
derived from these events and is never stored as a second, independent copy.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EventType(StrEnum):
    SESSION_CREATED = "session/created"
    SYSTEM_MESSAGE = "system/message"
    USER_MESSAGE = "user/message"
    TURN_START = "turn/start"
    TURN_END = "turn/end"
    STEP_START = "step/start"
    STEP_END = "step/end"
    ASSISTANT_MESSAGE = "assistant/message"
    TOOL_CALL = "tool/call"
    TOOL_RESULT = "tool/result"
    COMPACTION_START = "compaction/start"
    COMPACTION_SUMMARY = "compaction/summary"
    COMPACTION_END = "compaction/end"


class SessionEvent(BaseModel):
    """One immutable, lossless-JSON entry in a session log."""

    model_config = ConfigDict(extra="forbid", strict=True)

    seq: int = Field(ge=0)
    time: float
    type: EventType
    data: dict[str, Any]


class SessionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str
    player: str
    created_at: float
    updated_at: float
    last_seq: int = Field(ge=-1)


class AgentMemoryError(RuntimeError):
    """Base class for memory failures that callers may degrade around."""

    code = "MEMORY_ERROR"

    def __init__(self, message: str = "记忆模块操作失败。"):
        super().__init__(message)
        self.message = message


class MemoryStoreError(AgentMemoryError):
    code = "MEMORY_STORE_ERROR"


class MemoryCorruptError(AgentMemoryError):
    code = "MEMORY_CORRUPT"


class MemoryUnavailableError(AgentMemoryError):
    code = "MEMORY_UNAVAILABLE"


# Event data shape version. Bump when a migration is required.
MEMORY_FORMAT_VERSION = 1
