from langbridge_code.llm.client import _stream_chat_completion, from_chat_message
from langbridge_code.llm.trace import ThoughtEvent


class _DeltaFunction:
    def __init__(self, name=None, arguments=None):
        self.name = name
        self.arguments = arguments


class _DeltaToolCall:
    def __init__(self, index, call_id=None, name=None, arguments=None):
        self.index = index
        self.id = call_id
        self.function = _DeltaFunction(name=name, arguments=arguments)


class _Delta:
    def __init__(self, *, content=None, reasoning=None, tool_calls=None):
        self.content = content
        self.reasoning_content = reasoning
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, delta):
        self.delta = delta


class _Chunk:
    def __init__(self, delta):
        self.choices = [_Choice(delta)]


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


def test_stream_chat_completion_accumulates_message():
    chunks = [
        _Chunk(_Delta(reasoning="Plan ")),
        _Chunk(_Delta(reasoning="the page")),
        _Chunk(_Delta(content="Hello ")),
        _Chunk(_Delta(content="NY")),
        _Chunk(
            _Delta(
                tool_calls=[
                    _DeltaToolCall(0, call_id="call_1", name="read", arguments='{"path":'),
                    _DeltaToolCall(0, arguments=' "a.py"}'),
                ]
            )
        ),
    ]

    events: list[ThoughtEvent] = []

    class _Completions:
        @staticmethod
        def create(**_kwargs):
            return chunks

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    data = _stream_chat_completion(
        _Client(),
        {},
        label="Worker",
        stream_sink=events.append,
    )

    expected = _Message(
        content="Hello NY",
        tool_calls=[_ToolCall("call_1", "read", '{"path": "a.py"}')],
        reasoning="Plan the page",
    )
    assert data == {
        "output": from_chat_message(expected),
        "finish_reason": None,
    }
    assert any(event.kind == "reasoning_stream" for event in events)
    assert any(event.kind == "content_stream" for event in events)
    assert any(event.kind == "action_stream" for event in events)
