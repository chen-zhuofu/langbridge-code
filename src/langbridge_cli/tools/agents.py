import os

from langbridge_cli.config import DEFAULT_MODEL, load_api_key
from langbridge_cli.multi_agent import run_l3_test_engineer, run_l4_engineer


TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "ask_l3_test_engineer",
        "description": "Ask the L3 test engineer agent to inspect test quality and run relevant tests.",
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The behavior, feature, or test change the L3 test engineer should verify.",
                },
                "context": {
                    "type": "string",
                    "description": "Relevant implementation details, files, or concerns from the lead agent.",
                    "default": "",
                },
            },
            "required": ["task"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "ask_l4_engineer",
        "description": "Ask the L4 engineer agent to implement a task, add focused tests, and verify them.",
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The implementation task for the L4 engineer.",
                },
                "context": {
                    "type": "string",
                    "description": "Relevant product, code, or test context from the lead agent.",
                    "default": "",
                },
                "feedback": {
                    "type": "string",
                    "description": "Feedback from L3 that L4 should address.",
                    "default": "",
                },
            },
            "required": ["task"],
            "additionalProperties": False,
        },
    }
]

TOOLS = {}


def tool(name):
    def register(function):
        TOOLS[name] = function
        return function

    return register


@tool("ask_l3_test_engineer")
def ask_l3_test_engineer(task, context="", api_key=None, model=None, trace_sink=None):
    api_key = api_key or load_api_key()
    model = model or os.environ.get("LANGBRIDGE_MODEL", DEFAULT_MODEL)
    if trace_sink is None:
        return run_l3_test_engineer(api_key, model, task, context)
    return run_l3_test_engineer(api_key, model, task, context, trace_sink=trace_sink)


@tool("ask_l4_engineer")
def ask_l4_engineer(task, context="", feedback="", api_key=None, model=None, trace_sink=None, approval_callback=None):
    api_key = api_key or load_api_key()
    model = model or os.environ.get("LANGBRIDGE_MODEL", DEFAULT_MODEL)
    if trace_sink is None and approval_callback is None:
        return run_l4_engineer(api_key, model, task, context, feedback)
    return run_l4_engineer(
        api_key,
        model,
        task,
        context,
        feedback,
        trace_sink=trace_sink,
        approval_callback=approval_callback,
    )


