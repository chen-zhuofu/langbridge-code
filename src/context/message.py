"""Message list helpers for context stack and workflow."""


def recent_chat_turns(messages, *, max_turns=20, max_chars=12000):
    """Extract recent user/assistant turns, skipping system prompts and tool items."""
    turns = []
    for message in messages:
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        turns.append({"role": role, "content": content})

    if len(turns) > max_turns:
        turns = turns[-max_turns:]

    while turns and sum(len(turn["content"]) for turn in turns) > max_chars:
        turns.pop(0)
    return turns


def iter_tool_rounds(messages: list) -> list[tuple[int, list[int]]]:
    rounds: list[tuple[int, list[int]]] = []
    index = 0
    round_idx = 0
    while index < len(messages):
        item = messages[index]
        item_type = item.get("type")
        if item.get("role") or item_type not in {"reasoning", "function_call", "function_call_output"}:
            index += 1
            continue
        if item_type == "function_call_output":
            index += 1
            continue

        indices: list[int] = []
        saw_output = False
        while index < len(messages):
            current = messages[index]
            current_type = current.get("type")
            if current_type == "function_call_output":
                indices.append(index)
                saw_output = True
                index += 1
                continue
            if current_type == "function_call":
                if saw_output:
                    break
                indices.append(index)
                index += 1
                continue
            if current_type == "reasoning":
                indices.append(index)
                index += 1
                continue
            break

        if indices:
            rounds.append((round_idx, indices))
            round_idx += 1
    return rounds
