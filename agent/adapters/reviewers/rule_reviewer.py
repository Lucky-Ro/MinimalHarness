import re
from collections.abc import Iterable
from typing import Any

from agent.adapters.command_policy import safe_command_argv
from agent.adapters.tool_schemas import BUILTIN_SCHEMAS
from agent.domain.roles import Decision
from agent.domain.session import Session, ToolCall
from agent.domain.tool import Tool


_JSON_TYPES = {
    "string": (str,), "integer": (int,), "number": (float, int), "boolean": (bool,),
    "array": (list,), "object": (dict,), "null": (type(None),),
}


def _check_arguments(arguments: Any, schema: dict[str, Any]) -> None:
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    for key in schema.get("required", []):
        if key not in arguments:
            raise ValueError(f"missing required argument: {key}")
    properties = schema.get("properties", {})
    for key, value in arguments.items():
        if key not in properties and schema.get("additionalProperties") is False:
            raise ValueError(f"unknown argument: {key}")
        field = properties.get(key, {})
        types = field.get("type", [])
        types = [types] if isinstance(types, str) else types
        # bool subclasses int in Python but is a distinct JSON type.
        if types and not any(type(value) in _JSON_TYPES.get(kind, ()) for kind in types):
            raise ValueError(f"{key} must have type {' or '.join(types)}")
        if "enum" in field and value not in field["enum"]:
            raise ValueError(f"unsupported value for {key}")
        if isinstance(value, str) and (not value.strip() or len(value) > field.get("maxLength", len(value))):
            raise ValueError(f"{key} is empty or exceeds its maximum length")


class RuleBasedReviewer:
    def __init__(self, tools: Iterable[Tool] | None = None):
        self.schemas = BUILTIN_SCHEMAS if tools is None else {tool.name: tool.parameters for tool in tools}

    def review(self, session: Session, call: ToolCall) -> Decision:
        name = call.tool_name
        if not isinstance(name, str) or not name.strip() or name not in self.schemas:
            return Decision(False, "unknown or empty tool name")
        args = call.arguments
        try:
            _check_arguments(args, self.schemas[name])
            if name == "bash":
                safe_command_argv(args.get("command"))
            elif name == "calculator":
                expression = args.get("expression")
                if not isinstance(expression, str) or not re.fullmatch(r"[0-9eE.+*/()\s-]{1,1000}", expression):
                    raise ValueError("calculator accepts short arithmetic expressions only")
            elif name == "todo":
                action = args.get("action")
                if action == "add" and not args.get("title"):
                    raise ValueError("todo add requires a title")
                if action in ("complete", "delete") and not args.get("todo_id"):
                    raise ValueError(f"todo {action} requires todo_id")
        except ValueError as exc:
            return Decision(False, str(exc))
        return Decision(True)
