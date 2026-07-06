import copy


TOOL_PURPOSE_ARGUMENT = "purpose"


def with_tool_purpose(schemas):
    updated = copy.deepcopy(schemas)
    for schema in updated:
        parameters = schema.get("parameters", {})
        properties = parameters.setdefault("properties", {})
        properties[TOOL_PURPOSE_ARGUMENT] = {
            "type": "string",
            "description": "One short user-visible sentence explaining why this tool call is needed now.",
        }
        required = parameters.setdefault("required", [])
        if TOOL_PURPOSE_ARGUMENT not in required:
            required.append(TOOL_PURPOSE_ARGUMENT)
    return updated


def strip_tool_purpose(arguments):
    return {
        name: value
        for name, value in arguments.items()
        if name != TOOL_PURPOSE_ARGUMENT
    }
