import json

from langbridge_code.llm.client import from_chat_message, to_chat_messages, to_chat_tools


class _Fn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.function = _Fn(name, arguments)


class _Message:
    def __init__(self, content=None, tool_calls=None, reasoning=None):
        self.content = content
        self.tool_calls = tool_calls
        self.reasoning_content = reasoning


def test_to_chat_messages_with_tool_roundtrip():
    agent_input = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "read README"},
        {
            "type": "function_call",
            "name": "read_file",
            "call_id": "call_1",
            "arguments": json.dumps({"path": "README.md", "purpose": "inspect docs"}),
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "# Title"},
        {"role": "user", "content": "thanks"},
    ]

    messages = to_chat_messages(agent_input)

    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[2]["role"] == "assistant"
    assert messages[2]["tool_calls"][0]["function"]["name"] == "read_file"
    assert messages[3]["role"] == "tool"
    assert messages[3]["content"] == "# Title"
    assert messages[4]["role"] == "user"


def test_from_chat_message_maps_tool_calls():
    message = _Message(
        tool_calls=[_ToolCall("call_9", "run_tests", json.dumps({"path": "."}))],
    )
    output = from_chat_message(message)

    assert output[0]["type"] == "function_call"
    assert output[0]["name"] == "run_tests"
    assert output[0]["call_id"] == "call_9"


def test_to_chat_tools_wraps_function_schema():
    tools = to_chat_tools([
        {
            "type": "function",
            "name": "read_file",
            "description": "Read a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        }
    ])

    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == "read_file"
