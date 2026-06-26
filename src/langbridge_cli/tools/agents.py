import os

from langbridge_cli.config import DEFAULT_MODEL, load_api_key
from langbridge_cli.agents.multi_agent import run_l3_test_engineer


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
    },
    {
        "type": "function",
        "name": "ask_l5_engineer",
        "description": (
            "Ask the L5 senior engineer to take a HARD component task, split it into "
            "technical sub-tasks, and deliver it with L3 review on each sub-task."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The HARD component task for the L5 senior engineer.",
                },
                "context": {
                    "type": "string",
                    "description": "Relevant product, code, or test context from the lead agent.",
                    "default": "",
                },
                "feedback": {
                    "type": "string",
                    "description": "PM feedback on a previous L5 delivery that should be addressed.",
                    "default": "",
                },
            },
            "required": ["task"],
            "additionalProperties": False,
        },
    },
]

TOOLS = {}


def tool(name):
    def register(function):
        TOOLS[name] = function
        return function

    return register


@tool("ask_l3_test_engineer")
def ask_l3_test_engineer(task, context="", api_key=None, model=None, trace_sink=None, run_log_path=None, turn_id=None):
    api_key = api_key or load_api_key()
    model = model or os.environ.get("LANGBRIDGE_MODEL", DEFAULT_MODEL)
    if trace_sink is None and run_log_path is None:
        return run_l3_test_engineer(api_key, model, task, context)
    return run_l3_test_engineer(
        api_key,
        model,
        task,
        context,
        trace_sink=trace_sink,
        run_log_path=run_log_path,
        turn_id=turn_id,
    )


@tool("ask_l4_engineer")
def ask_l4_engineer(task, context="", feedback=""):
    # The living L4<->L3 review loop runs in the PM runtime; run_tool_call dispatches
    # to agent.run_l4_component and overrides this placeholder output.
    return ""


@tool("ask_l5_engineer")
def ask_l5_engineer(task, context="", feedback=""):
    # The L5 component Ralph loop runs in the PM runtime; run_tool_call dispatches
    # to agent.run_l5_component and overrides this placeholder output.
    return ""


