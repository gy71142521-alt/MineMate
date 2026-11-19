from copy import deepcopy
from typing import Any


class ReferenceError(ValueError):
    pass


def resolve(value: Any, results: dict, allowed: set[str], depth: int = 0) -> Any:
    """仅支持先前步骤的 JSON dict/list 路径，不支持表达式、属性或代码。"""
    if depth > 8:
        raise ReferenceError("reference depth")
    if isinstance(value, dict):
        if "$ref" in value:
            ref = value["$ref"]
            if set(value) != {"$ref"} or not isinstance(ref, dict) or set(ref) != {"step", "path"}:
                raise ReferenceError("reference format")
            step, path = ref["step"], ref["path"]
            if not isinstance(step, str) or step not in allowed or not isinstance(path, list) or not 1 <= len(path) <= 8:
                raise ReferenceError("reference target")
            if any(type(part) not in (str, int) or isinstance(part, str) and (part.startswith("_") or part in ("constructor", "prototype")) for part in path):
                raise ReferenceError("reference path")
            if step not in results:
                return None  # 提交校验只检查引用结构；运行时必须存在真实前序结果。
            current = results[step]
            for part in path:
                if isinstance(current, dict) and type(part) is str and part in current:
                    current = current[part]
                elif isinstance(current, list) and type(part) is int and 0 <= part < len(current):
                    current = current[part]
                else:
                    raise ReferenceError("reference missing")
            return deepcopy(current)
        return {key: resolve(item, results, allowed, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(item, results, allowed, depth + 1) for item in value]
    return value


def validate_template(schema: dict, value: Any, previous: set[str]) -> bool:
    from ..tool_client import validate
    if isinstance(value, dict) and "$ref" in value:
        resolve(value, {}, previous)
        return True
    if schema.get("type") == "object" and isinstance(value, dict):
        return all(key in value for key in schema["required"]) and all(
            key in schema["properties"] and validate_template(schema["properties"][key], item, previous) for key, item in value.items())
    return validate(schema, value)
