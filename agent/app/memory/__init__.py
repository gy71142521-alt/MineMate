"""MineMate durable memory and context assembly.

Public surface:
- `MemoryManager` / `MemoryConfig` — per-player orchestration
- `SQLiteEventStore` / `InMemoryEventStore` — durable event logs
- `Session` — append-only log + derived surface
"""

from .compaction import (
    AdaptiveSummarizer,
    CompactionPolicy,
    CompactionResult as MemoryCompactionResult,
    DeterministicSummarizer,
    ModelSummarizer,
)
from .context import compose_system_prompt, ephemeral_context
from .manager import MemoryConfig, MemoryManager, MemoryTurn
from .session import Session
from .store import EventStore, InMemoryEventStore, SQLiteEventStore
from .types import (
    AgentMemoryError,
    EventType,
    MemoryCorruptError,
    MemoryStoreError,
    SessionEvent,
    SessionRecord,
)

__all__ = [
    "AdaptiveSummarizer",
    "AgentMemoryError",
    "CompactionPolicy",
    "DeterministicSummarizer",
    "EventStore",
    "EventType",
    "InMemoryEventStore",
    "MemoryCompactionResult",
    "MemoryConfig",
    "MemoryCorruptError",
    "MemoryManager",
    "MemoryStoreError",
    "MemoryTurn",
    "ModelSummarizer",
    "SQLiteEventStore",
    "Session",
    "SessionEvent",
    "SessionRecord",
    "compose_system_prompt",
    "ephemeral_context",
]
