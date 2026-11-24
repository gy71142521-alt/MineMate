"""Python-owned control tools for the planned runtime.

These tools are not registered in Node: they affect the orchestration loop, not
Minecraft. Node remains the only source for game tool schemas.
"""

from __future__ import annotations


FINISH_TOOL = {
    "type": "function",
    "function": {
        "name": "finish_task",
        "description": "结束当前任务。只有真正完成、部分完成或无法继续时才调用；不要用一段普通文字代替结束。",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["completed", "partial", "blocked"]},
                "summary": {"type": "string", "minLength": 1, "maxLength": 300},
                "remaining": {"type": "string", "maxLength": 300},
            },
            "required": ["status", "summary"],
            "additionalProperties": False,
        },
    },
}


UPDATE_PLAN_TOOL = {
    "type": "function",
    "function": {
        "name": "update_plan",
        "description": "根据真实结果修改剩余计划；步骤是结果目标，不是固定工具调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "maxLength": 300},
                "goal": {"type": "string", "maxLength": 300},
                "steps": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 8,
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string", "minLength": 1, "maxLength": 200},
                            "success_criteria": {"type": "string", "minLength": 1, "maxLength": 300},
                            "status": {"type": "string", "enum": ["pending", "in_progress", "done", "blocked"]},
                        },
                        "required": ["title", "success_criteria"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["steps"],
            "additionalProperties": False,
        },
    },
}


CONTROL_TOOL_NAMES = frozenset({"finish_task", "update_plan"})
