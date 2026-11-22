"""Bounded history compaction.

Compaction replaces the oldest complete turns with one checkpoint summary. The
event log keeps the original events; `compaction/summary.shadowed_seqs` marks
them as no longer part of the model surface. Recent turns remain verbatim, and
tool calls are only compacted together with their results.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from .session import CHECKPOINT_PREAMBLE, SUMMARY_CLOSE, SUMMARY_OPEN, Session
from .tokens import estimate_messages, estimate_text, estimate_tools, truncate_text
from .types import EventType, SessionEvent

logger = logging.getLogger("minemate.memory.compaction")


@dataclass(frozen=True)
class CompactionPolicy:
    context_window: int
    threshold_ratio: float = 0.75
    retain_turns: int = 6
    max_summary_chars: int = 4000
    max_transcript_chars: int = 24000

    def threshold_tokens(self) -> int:
        return max(512, int(self.context_window * self.threshold_ratio))


@dataclass(frozen=True)
class CompactionResult:
    summary: str
    shadowed_seqs: tuple[int, ...]
    tokens_before: int
    tokens_after: int
    start_seq: int
    summary_seq: int
    end_seq: int


class Summarizer(Protocol):
    name: str

    async def summarize(self, transcript: str, *, max_chars: int) -> str: ...


class DeterministicSummarizer:
    """Extractive fallback: no extra model call, always available."""

    name = "deterministic"

    async def summarize(self, transcript: str, *, max_chars: int) -> str:
        body = truncate_text(transcript.strip(), max_chars - 32)
        return (
            "## 对话压缩摘要\n"
            "以下为较早轮次的提取式摘要（非模型生成），用于保留事实与未完成事项：\n\n"
            f"{body}"
        )


class ModelSummarizer:
    """Use the configured chat model for a structured checkpoint."""

    def __init__(self, llm: Any):
        self.llm = llm
        self.name = getattr(getattr(llm, "settings", None), "llm_model", "model")

    async def summarize(self, transcript: str, *, max_chars: int) -> str:
        text = await self.llm.summarize(transcript, max_tokens=max(256, min(1200, max_chars // 3)))
        return truncate_text(str(text).strip(), max_chars)


class AdaptiveSummarizer:
    """Prefer the model, but never let summary failure block gameplay."""

    name = "adaptive"

    def __init__(self, model_summarizer: ModelSummarizer | None, fallback: DeterministicSummarizer | None = None):
        self.model_summarizer = model_summarizer
        self.fallback = fallback or DeterministicSummarizer()

    async def summarize(self, transcript: str, *, max_chars: int) -> str:
        if self.model_summarizer is not None:
            try:
                text = await self.model_summarizer.summarize(transcript, max_chars=max_chars)
                if text.strip():
                    return text
                logger.warning("[Memory] 模型摘要为空，使用提取式摘要。")
            except Exception as error:
                logger.warning("[Memory] 模型摘要失败，使用提取式摘要：%s", type(error).__name__)
        return await self.fallback.summarize(transcript, max_chars=max_chars)


def build_transcript(events: list[SessionEvent], *, max_chars: int) -> str:
    lines: list[str] = []
    for event in events:
        data = event.data
        if event.type == EventType.USER_MESSAGE:
            lines.append(f"[玩家] {truncate_text(str(data.get('text', '')), 800)}")
        elif event.type == EventType.ASSISTANT_MESSAGE:
            content = data.get("content")
            if isinstance(content, str) and content.strip():
                lines.append(f"[MineMate] {truncate_text(content, 800)}")
            for call in data.get("tool_calls") or []:
                function = call.get("function") if isinstance(call, dict) else None
                name = function.get("name") if isinstance(function, dict) else "?"
                arguments = function.get("arguments") if isinstance(function, dict) else ""
                lines.append(f"[调用] {name} {truncate_text(str(arguments), 200)}")
        elif event.type == EventType.TOOL_RESULT:
            result = data.get("result") if isinstance(data.get("result"), dict) else {}
            status = result.get("data", {}).get("status") if isinstance(result.get("data"), dict) else None
            error = result.get("error") if isinstance(result.get("error"), dict) else None
            summary = {"success": result.get("success"), "status": status,
                       "error": error.get("code") if error else None}
            try:
                payload = json.dumps(result.get("data"), ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError, RecursionError):
                payload = ""
            lines.append(
                f"[工具] {data.get('tool')} {json.dumps(summary, ensure_ascii=False)} data={truncate_text(payload, 400)}"
            )
    text = "\n".join(lines)
    return truncate_text(text, max_chars)


def _events_by_completed_turn(session: Session) -> dict[int, list[SessionEvent]]:
    groups: dict[int, list[SessionEvent]] = {}
    current: int | None = None
    for event in session.events:
        if event.type == EventType.TURN_START:
            current = int(event.data["turn"])
            groups.setdefault(current, [])
        if current is None:
            continue
        if event.type in {EventType.SYSTEM_MESSAGE, EventType.COMPACTION_START,
                          EventType.COMPACTION_SUMMARY, EventType.COMPACTION_END,
                          EventType.SESSION_CREATED}:
            continue
        groups[current].append(event)
        if event.type == EventType.TURN_END:
            current = None
    return groups


def select_events(session: Session, current_turn: int, retain_turns: int) -> tuple[list[SessionEvent], list[int]]:
    """Select oldest complete turns, never splitting a turn.

    The current open turn is excluded. At least one turn must remain verbatim.
    """
    groups = _events_by_completed_turn(session)
    shadowed = session.shadowed_seqs()
    completed = sorted(turn for turn in groups if turn < current_turn)
    # A group is complete only if its last event is turn/end and it is not
    # already covered by the latest summary.
    complete = [
        turn for turn in completed
        if groups[turn] and groups[turn][-1].type == EventType.TURN_END
        and not all(event.seq in shadowed for event in groups[turn])
    ]
    if len(complete) <= retain_turns:
        return [], []
    selected_turns = complete[:len(complete) - max(1, retain_turns)]
    selected = [event for turn in selected_turns for event in groups[turn]]
    return selected, selected_turns


def _checkpoint_tokens(summary: str) -> int:
    return estimate_text(f"{CHECKPOINT_PREAMBLE}\n{SUMMARY_OPEN}\n{summary}\n{SUMMARY_CLOSE}")


async def compact_if_needed_async(
    session: Session,
    policy: CompactionPolicy,
    messages: list[dict[str, Any]],
    tools: Any,
    current_turn: int,
    summarizer: Summarizer,
    *,
    source: str = "auto",
) -> CompactionResult | None:
    tokens_before = estimate_messages(messages) + estimate_tools(tools)
    if tokens_before <= policy.threshold_tokens():
        return None
    selected, selected_turns = select_events(session, current_turn, policy.retain_turns)
    if not selected:
        return None

    previous_summary = session.latest_summary()
    transcript = build_transcript(selected, max_chars=policy.max_transcript_chars)
    if previous_summary:
        transcript = (
            "此前已经压缩过的摘要：\n"
            f"{truncate_text(str(previous_summary.data.get('summary', '')), policy.max_summary_chars)}\n\n"
            "本段待压缩历史：\n" + transcript
        )
    start = session.append(EventType.COMPACTION_START, {"turn": current_turn, "source": source})
    try:
        summary = await summarizer.summarize(transcript, max_chars=policy.max_summary_chars)
        summary = truncate_text(summary.strip(), policy.max_summary_chars)
        if not summary:
            raise ValueError("empty summary")
        tokens_after = _checkpoint_tokens(summary)
        if tokens_after >= tokens_before:
            raise ValueError("summary did not shrink the context")
        previous_seqs = set(session.shadowed_seqs())
        if previous_summary:
            previous_seqs.add(previous_summary.seq)
        shadowed = sorted(previous_seqs | {event.seq for event in selected})
        summary_event = session.append(EventType.COMPACTION_SUMMARY, {
            "summary": summary,
            "shadowed_seqs": shadowed,
            "shadowed_turns": selected_turns,
            "tokens_before": tokens_before,
            "tokens_after": tokens_after,
            "source": source,
            "summarizer": summarizer.name,
        })
        end = session.append(EventType.COMPACTION_END, {"turn": current_turn, "error": None})
        logger.info("[Memory] 压缩完成 turns=%s events=%d tokens=%d->%d summarizer=%s",
                    selected_turns, len(selected), tokens_before, tokens_after, summarizer.name)
        return CompactionResult(summary=summary, shadowed_seqs=tuple(shadowed),
                                tokens_before=tokens_before, tokens_after=tokens_after,
                                start_seq=start.seq, summary_seq=summary_event.seq, end_seq=end.seq)
    except Exception as error:
        session.append(EventType.COMPACTION_END, {
            "turn": current_turn,
            "error": {"type": type(error).__name__, "message": str(error)[:500]},
        })
        logger.warning("[Memory] 压缩失败，保留原历史：%s", type(error).__name__)
        return None
