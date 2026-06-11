from langbridge_cli.debug import print_llm_request, print_llm_response


def test_llm_debug_output_is_disabled_by_default(capsys, monkeypatch):
    monkeypatch.delenv("LANGBRIDGE_DEBUG_LLM", raising=False)

    print_llm_request("PM agent", "model", [{"role": "user", "content": "hello"}])
    print_llm_response("PM agent", {"output": []})

    assert capsys.readouterr().out == ""


def test_llm_debug_output_formats_request_and_response(capsys, monkeypatch):
    monkeypatch.setenv("LANGBRIDGE_DEBUG_LLM", "1")

    print_llm_request(
        "PM agent",
        "model",
        [{"role": "user", "content": "implement calculator"}],
        [{"name": "ask_l4_engineer"}],
    )
    print_llm_response(
        "PM agent",
        {
            "output": [
                {
                    "type": "function_call",
                    "name": "ask_l4_engineer",
                    "arguments": '{"task":"implement calculator"}',
                    "call_id": "call_1",
                }
            ]
        },
    )

    output = capsys.readouterr().out
    assert output == (
        "[LLM DEBUG] PM agent input: 0. user: implement calculator\n"
        '[LLM DEBUG] PM agent output: 0. function_call ask_l4_engineer({"task":"implement calculator"}) '
        "call_id=call_1\n"
    )


def test_llm_debug_output_truncates_long_items(capsys, monkeypatch):
    monkeypatch.setenv("LANGBRIDGE_DEBUG_LLM", "1")
    monkeypatch.setenv("LANGBRIDGE_DEBUG_LLM_MAX_CHARS", "200")
    long_text = "x" * 300

    print_llm_request(
        "PM agent",
        "model",
        [
            {"role": "user", "content": long_text},
            {
                "type": "function_call",
                "name": "edit_file",
                "arguments": '{"old":"' + long_text + '"}',
                "call_id": "call_1",
            },
            {"type": "function_call_output", "call_id": "call_1", "output": long_text},
        ],
    )

    output = capsys.readouterr().out
    debug_lines = output.splitlines()
    assert debug_lines
    assert all(len(line) <= 203 for line in debug_lines)
    assert "x" * 250 not in output


def test_llm_debug_output_skips_non_agent_labels(capsys, monkeypatch):
    monkeypatch.setenv("LANGBRIDGE_DEBUG_LLM", "1")

    print_llm_request("session summary", "model", [{"role": "user", "content": "hello"}])
    print_llm_response("session summary", {"output": []})

    assert capsys.readouterr().out == ""
