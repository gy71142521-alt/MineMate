"""Python-owned control tools for the planned runtime.

These tools are not registered in Node: they affect the orchestration loop, not
Minecraft. Node remains the only source for game tool schemas.
"""

from __future__ import annotations

from copy import deepcopy


def skill_first_toolset(node_tools: list[dict], skill_definitions: list[dict]) -> tuple[list[dict], dict[str, str]]:
    """构建模型可见视图；完整 Node 目录继续供 Skill/HTTP 执行使用。

    这里只维护路由关系，不复制游戏 Schema。只有已注册的 Skill 才能替代入口。
    精确坐标挖掘、战斗索敌和独立熔炉操作仍需保留底层工具。
    """
    names = {item["name"] for item in skill_definitions}
    replacements = {}
    if "craft_item" in names:
        replacements["craft_item"] = "craft_item"
    if "explore_and_find" in names:
        replacements["find_block"] = "explore_and_find"
        # find_entity 同时供战斗索敌使用，不能将所有实体查询强制变成搜索 Skill。
    if "smelt_item" in names:
        replacements["smelt_item"] = "smelt_item"
    preferences = {
        "mine_block": ("gather_resource", "仅用于挖掘玩家明确指定坐标的单个方块。按资源名称/数量采集必须优先 execute_skill(skill=gather_resource)，不要手动组合挖掘与拾取。"),
        "find_entity": ("explore_and_find", "按类型寻找/接近生物优先 execute_skill(skill=explore_and_find)；本工具保留用于战斗索敌和精确实体筛选。"),
        "furnace_add_input": ("smelt_item", "整体熔炼目标优先 execute_skill(skill=smelt_item)；本工具仅用于独立添加/补充指定熔炉的原料。"),
        "furnace_add_fuel": ("smelt_item", "整体熔炼目标优先 execute_skill(skill=smelt_item)；本工具仅用于独立添加/补充指定熔炉的燃料。"),
    }
    visible = []
    for original in node_tools:
        definition = deepcopy(original)
        function = definition["function"]
        name = function["name"]
        if name in replacements:
            if name != "smelt_item":
                continue
            actions = function["parameters"].get("properties", {}).get("action", {})
            if "enum" not in actions:
                continue
            actions["enum"] = [action for action in actions["enum"] if action != "start"]
            if not actions["enum"]:
                continue
            function["description"] = "仅查询/取出旧 smelt_item 任务的产物；启动熔炼必须使用 execute_skill(skill=smelt_item)。"
        if name in preferences and preferences[name][0] in names:
            function["description"] = preferences[name][1] + " " + function["description"]
        visible.append(definition)
    present = {item["function"]["name"] for item in node_tools}
    return visible, {tool: skill for tool, skill in replacements.items() if tool in present}


FINISH_TOOL = {
    "type": "function",
    "function": {
        "name": "finish_task",
        "description": "结束当前任务。只有真正完成、部分完成或无法继续时才调用；不要用一段普通文字代替结束。summary 是给玩家的自然聊天，不要写坐标、材料清单或系统状态。",
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


def execute_skill_tool(skill_definitions: list[dict]) -> dict:
    """Build the Python-owned Skill dispatch tool from the live Skill Registry.

    Skill parameters remain validated by SkillRegistry. The outer schema keeps
    arguments open so the Node-owned tool schemas are not duplicated here.
    """
    names = [item.get("name") for item in skill_definitions
             if isinstance(item, dict) and isinstance(item.get("name"), str)]
    details = "; ".join(
        f"{item['name']}: {item.get('description', '')}; parameters={item.get('parameters', {})}"
        for item in skill_definitions if isinstance(item, dict) and isinstance(item.get("name"), str)
    )
    return {
        "type": "function",
        "function": {
            "name": "execute_skill",
            "description": "优先入口：搜索、采集、合成、熔炼目标应调用对应已注册 Skill，不要自行拼接其底层工具。Skill 会校验参数、调用真实 Node 工具并独立核验结果。缺材料或设施时按真实结果补前置步骤，再调用 Skill；不要绕回同名 Node 工具。可用 Skill：" + details[:6000],
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {"type": "string", "enum": names},
                    "arguments": {"type": "object", "additionalProperties": True},
                },
                "required": ["skill", "arguments"],
                "additionalProperties": False,
            },
        },
    }


CONTROL_TOOL_NAMES = frozenset({"finish_task", "update_plan", "execute_skill"})
