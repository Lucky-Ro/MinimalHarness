"""Function parameters shared by registered tools and the offline rule reviewer."""


BUILTIN_SCHEMAS = {
    "todo": {
        "type": "object", "additionalProperties": False, "required": ["action"],
        "properties": {
            "action": {"type": "string", "enum": ["add", "list", "complete", "delete"]},
            "title": {"type": "string", "description": "添加待办时填写标题"},
            "todo_id": {"type": "string", "description": "完成或删除时填写待办 ID"},
        },
    },
    "weather": {
        "type": "object", "additionalProperties": False, "required": ["city"],
        "properties": {"city": {"type": "string"}, "date": {"type": "string", "default": "today"}},
    },
    "calculator": {
        "type": "object", "additionalProperties": False, "required": ["expression"],
        "properties": {"expression": {"type": "string", "maxLength": 1000}},
    },
    "search": {
        "type": "object", "additionalProperties": False, "required": ["query"],
        "properties": {"query": {"type": "string", "maxLength": 200}},
    },
    "bash": {
        "type": "object", "additionalProperties": False, "required": ["command"],
        "properties": {"command": {"type": "string", "maxLength": 1000}},
    },
}
