"""Model-visible context contributions.

DSH treats instructions, time and references as user-visible context rather
than hidden prompt magic. MineMate keeps the same separation: durable history
lives in the event log; the current world snapshot and clock are ephemeral
contributions added to the request that produced them.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .tokens import truncate_text


MEMORY_SYSTEM_SUFFIX = (
    "\n\n你会收到此前对话的压缩摘要和最近对话。把摘要当作已经确认的背景，"
    "不要复述它；如果摘要与最近消息冲突，以最近消息和当前游戏状态为准。"
    "不要声称记得未出现在上下文中的事情；不确定时先查询或询问。"
)


def compose_system_prompt(base: str) -> str:
    return base.rstrip() + MEMORY_SYSTEM_SUFFIX


def compact_world_snapshot(snapshot: Any, *, max_chars: int = 6000) -> str:
    """Keep the decision-relevant parts of a Minecraft snapshot.

    The full snapshot remains available to tools; this projection is only for
    conversational context and is deliberately bounded.
    """
    if not isinstance(snapshot, dict):
        return "不可用"
    player = snapshot.get("player") if isinstance(snapshot.get("player"), dict) else {}
    inventory = snapshot.get("inventory") if isinstance(snapshot.get("inventory"), list) else []
    entities = snapshot.get("nearbyEntities") if isinstance(snapshot.get("nearbyEntities"), list) else []
    blocks = snapshot.get("nearbyBlocks") if isinstance(snapshot.get("nearbyBlocks"), list) else []

    item_totals: dict[str, int] = {}
    for item in inventory[:256]:
        if isinstance(item, dict) and isinstance(item.get("name"), str) and type(item.get("count")) is int:
            item_totals[item["name"]] = item_totals.get(item["name"], 0) + item["count"]
    top_items = sorted(item_totals.items(), key=lambda pair: (-pair[1], pair[0]))[:32]

    projected = {
        "timestamp": snapshot.get("timestamp"),
        "availability": snapshot.get("availability"),
        "player": {
            key: player.get(key) for key in
            ("health", "food", "position", "dimension", "experienceLevel", "alive")
        },
        "inventory": [{"name": name, "count": count} for name, count in top_items],
        "nearbyEntities": [
            {"name": item.get("name"), "category": item.get("category"), "distance": item.get("distance")}
            for item in entities[:12] if isinstance(item, dict)
        ],
        "nearbyBlocks": [
            {"name": item.get("name"), "position": item.get("position"), "distance": item.get("distance")}
            for item in blocks[:12] if isinstance(item, dict)
        ],
        "truncated": {"inventory": len(inventory) > 256, "entities": len(entities) > 12, "blocks": len(blocks) > 12},
    }
    text = json.dumps(projected, ensure_ascii=False, allow_nan=False)
    return truncate_text(text, max_chars)


def ephemeral_context(snapshot: Any, *, player: str | None = None,
                      now: datetime | None = None,
                      max_world_chars: int = 6000) -> list[dict[str, Any]]:
    stamp = (now or datetime.now(timezone.utc)).astimezone().isoformat(timespec="seconds")
    world = compact_world_snapshot(snapshot, max_chars=max_world_chars)
    return [
        {
            "role": "user",
            "content": (
                "以下是本轮开始时的真实游戏状态，仅用于理解当前局面，不是玩家发言。\n"
                f"<speaker>{player or '未知'}</speaker>\n"
                f"<world>\n{world}\n</world>\n<local_time>{stamp}</local_time>"
            ),
        }
    ]

