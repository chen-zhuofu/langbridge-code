from langbridge_code.llm.debug import print_llm_request, print_llm_response


def test_llm_debug_output_is_disabled_by_default(capsys, monkeypatch):
    monkeypatch.delenv("LANGBRIDGE_DEBUG_LLM", raising=False)

    print_llm_request("Coder", "model", [{"role": "user", "content": "hello"}])
    print_llm_response("Coder", {"output": []})

    assert capsys.readouterr().out == ""


def test_llm_debug_output_formats_request_and_response(capsys, monkeypatch):
    monkeypatch.setenv("LANGBRIDGE_DEBUG_LLM", "1")
    monkeypatch.setenv("LANGBRIDGE_DEBUG_LLM_MAX_CHARS", "500")

    print_llm_request("Coder", "model", [{"role": "user", "content": "implement calculator"}])
    print_llm_response(
        "Coder",
        {
            "output": [
                {"type": "reasoning", "summary": []},
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Implement the calculator."}],
                },
                {
                    "type": "function_call",
                    "name": "update_plan",
                    "arguments": '{"purpose":"Write the todo list.","content":"# Todo"}',
                    "call_id": "call_1",
                }
            ]
        },
    )

    output = capsys.readouterr().out
    assert output == (
        "[LLM DEBUG] Coder output: 1. message: Implement the calculator. | "
        '2. purpose: Write the todo list. -> function_call '
        'update_plan({"content":"# Todo"}) '
        "call_id=call_1\n"
    )
    assert "reasoning" not in output


def test_llm_debug_output_truncates_long_items(capsys, monkeypatch):
    monkeypatch.setenv("LANGBRIDGE_DEBUG_LLM", "1")
    monkeypatch.setenv("LANGBRIDGE_DEBUG_LLM_MAX_CHARS", "200")
    long_text = "x" * 300

    print_llm_response(
        "Coder",
        {
            "output": [
                {
                    "type": "function_call",
                    "name": "Edit",
                    "arguments": '{"purpose":"' + long_text + '","old":"' + long_text + '"}',
                    "call_id": "call_1",
                }
            ]
        },
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
