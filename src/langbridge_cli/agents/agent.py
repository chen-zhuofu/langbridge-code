import copy
import inspect
import json
import re
import sys

from langbridge_cli.llm.client import create_model_response
from langbridge_cli.settings import (
    MAX_AGENT_CONTEXT_TOKENS,
    MAX_AGENT_SECONDS,
    MAX_AGENT_STEPS,
    MAX_L4_L3_SECONDS,
    MAX_L4_L3_TURNS,
    MAX_L5_RALPH_TURNS,
    MAX_PM_LOOPS,
    MAX_PM_SECONDS,
    WRITE_TOOLS,
)
from langbridge_cli.agents.roles import SYSTEM_PROMPT
from langbridge_cli import policy
from langbridge_cli.tools.plan import read_todo_list
from langbridge_cli.persistence.logging import (
    write_finish_log,
    write_input_log,
    write_tool_calls_log,
    write_tool_calls_result_log,
)
from langbridge_cli.llm.parse import extract_output_text, print_step_trace
from langbridge_cli.llm.tool_schema import strip_tool_purpose
from langbridge_cli.tools import MAIN_TOOL_SCHEMAS, MAIN_TOOLS
from langbridge_cli.persistence.agent_worklog import (
    new_worklog_id,
    write_worklog_finish,
    write_worklog_observation,
    write_worklog_received,
    write_worklog_step,
)
from langbridge_cli.persistence.worklog import append_worklog_entry, start_worklog
from langbridge_cli.agents.limits import now, over_context_budget, over_time_budget
from langbridge_cli.agents import control
from langbridge_cli.persistence.context import (
    RecentFileStore,
    compact_messages_if_needed,
    record_tool_read,
)


def run_pm_loop(
    api_key,
    model,
    target,
    run_log_path,
    turn_id,
    trace_sink=None,
    print_reply=True,
    approval_callback=None,
    messages=None,
):
    """Run one user turn as the PM agentic loop.

    Both entry points (the TUI and the plain REPL) drive turns through here so
    they behave identically apart from the UI. `messages` is the one growing
    conversation kept by the caller, so the PM remembers the whole session.
    The PM keeps working while it reports BUG_STATUS: OPEN, up to MAX_PM_LOOPS.
    """
    if messages is None:
        messages = [{"role": "system", "content": policy.apply("pm", SYSTEM_PROMPT)}]
    messages.append({"role": "user", "content": pm_round_prompt(target, read_todo_list(run_log_path))})

    write_input_log(run_log_path, turn_id, messages)
    worklog_id = new_worklog_id(run_log_path, "PM agent")
    received = next((message.get("content", "") for message in reversed(messages) if message.get("role") == "user"), "")
    write_worklog_received(run_log_path, "PM agent", worklog_id, turn_id, str(received))
    turn_start = now()
    episode_start = turn_start
    file_store = RecentFileStore()
    pm_round = 0
    step = 0
    while step < MAX_AGENT_STEPS:
        control.checkpoint()
        if over_time_budget(turn_start, MAX_PM_SECONDS):
            return finish_pm(messages, "Agent stopped because it ran out of time.", run_log_path, turn_id, print_reply, worklog_id)
        if over_time_budget(episode_start, MAX_AGENT_SECONDS):
            return finish_pm(messages, "Agent stopped because it ran out of time.", run_log_path, turn_id, print_reply, worklog_id)
        if over_context_budget(messages, MAX_AGENT_CONTEXT_TOKENS):
            return finish_pm(messages, "Agent stopped because it exceeded the context budget.", run_log_path, turn_id, print_reply, worklog_id)
        step_response = control.run_interruptible(lambda: create_response(api_key, model, messages)).get("output", [])
        tool_calls = [item for item in step_response if item.get("type") == "function_call"]
        print_step_trace(step_response, include_message=bool(tool_calls), label="PM agent", sink=trace_sink)

        if tool_calls:
            messages.extend(step_response)
            write_tool_calls_log(run_log_path, turn_id, step, step_response)
            write_worklog_step(run_log_path, "PM agent", worklog_id, turn_id, step, step_response)
            for call in tool_calls:
                tool_output = run_tool_call(call, api_key, model, trace_sink, approval_callback, run_log_path, turn_id)
                record_tool_read(file_store, call.get("name"), call.get("arguments"), tool_output.get("output", ""))
                messages.append(tool_output)
                write_tool_calls_result_log(run_log_path, turn_id, step, tool_output)
                write_worklog_observation(run_log_path, "PM agent", worklog_id, turn_id, step, tool_output)
            compact_messages_if_needed(messages, max_context_tokens=MAX_AGENT_CONTEXT_TOKENS, file_store=file_store, api_key=api_key, model=model, label="PM compaction")
            step += 1
            continue

        finished = extract_output_text(step_response)
        if pm_should_continue(finished) and (pm_round + 1) < MAX_PM_LOOPS:
            pm_round += 1
            messages.append({"role": "assistant", "content": finished})
            messages.append({"role": "user", "content": pm_continue_prompt(read_todo_list(run_log_path))})
            episode_start = now()
            continue
        return finish_pm(messages, finished, run_log_path, turn_id, print_reply, worklog_id)
    return finish_pm(messages, "Agent stopped because it reached the maximum tool-call steps.", run_log_path, turn_id, print_reply, worklog_id)


def pm_round_prompt(target, todo_list):
    parts = [f"Latest user message:\n{target}"]
    if todo_list:
        parts.append(
            "todo_list from earlier work (continue it only if the message above is "
            f"a development task, not conversation):\n{todo_list}"
        )
    else:
        parts.append("There is no todo_list yet.")
    return "\n\n".join(parts)


def pm_continue_prompt(todo_list):
    parts = [
        "There is still an open bug. Keep working: re-check each subtask and the "
        "end-to-end test, and for anything still broken add a comment and send it "
        "back to the engineer to fix. When everything passes, wrap up."
    ]
    if todo_list:
        parts.append(f"Current todo_list:\n{todo_list}")
    else:
        parts.append("There is no todo_list yet.")
    return "\n\n".join(parts)


_BUG_STATUS_RE = re.compile(r"\s*BUG_STATUS:\s*([A-Za-z]+)\s*$", re.IGNORECASE)


def pm_should_continue(finished):
    # The PM may emit BUG_STATUS on its own line or inline after the reply, so
    # match the trailing token directly instead of relying on line splits.
    match = _BUG_STATUS_RE.search(finished)
    return bool(match) and match.group(1).upper() == "OPEN"


def strip_bug_status(finished):
    """Drop the PM's trailing BUG_STATUS control token before showing the reply.

    BUG_STATUS drives pm_should_continue, not the user, so it should not surface
    in the printed reply. The PM sometimes puts it on its own line and sometimes
    inline, so we strip it from the end either way.
    """
    return _BUG_STATUS_RE.sub("", finished.rstrip()).rstrip()


def finish_pm(input, finished, run_log_path, turn_id, print_reply, worklog_id=None):
    input.append({"role": "assistant", "content": finished})
    write_finish_log(run_log_path, turn_id, finished)
    write_worklog_finish(run_log_path, "PM agent", worklog_id, turn_id, finished)
    if print_reply:
        print(f"\n{strip_bug_status(finished)}\n")
    return finished


def create_response(api_key, model, agent_input):
    return create_model_response(
        api_key,
        model,
        agent_input,
        tool_schemas=MAIN_TOOL_SCHEMAS,
        reasoning={"summary": "auto"},
        label="PM agent",
    )


def run_tool_call(call, api_key=None, model=None, trace_sink=None, approval_callback=None, run_log_path=None, turn_id=None):
    name = call.get("name")
    call_id = call.get("call_id")

    try:
        arguments = strip_tool_purpose(json.loads(call.get("arguments") or "{}"))
        if name not in MAIN_TOOLS:
            raise ValueError(f"Unknown tool: {name}")
        if name in WRITE_TOOLS and not approve_write_tool(name, arguments, approval_callback):
            raise PermissionError(f"{name} was not approved")
        if name == "ask_l4_engineer":
            # The registered tool is just a placeholder; the living L4<->L3 loop runs here.
            output = run_l4_component(api_key, model, arguments, trace_sink, run_log_path, turn_id, approval_callback)
        elif name == "ask_l5_engineer":
            # The registered tool is just a placeholder; the L5 component loop runs here.
            output = run_l5_component(api_key, model, arguments, trace_sink, run_log_path, turn_id, approval_callback)
        else:
            tool_arguments = add_hidden_tool_context(MAIN_TOOLS[name], arguments, api_key, model, trace_sink, approval_callback, run_log_path, turn_id)
            output = MAIN_TOOLS[name](**tool_arguments)
    except Exception as error:
        output = f"Tool error: {error}"

    return {"type": "function_call_output", "call_id": call_id, "output": output}


def run_l4_component(api_key, model, arguments, trace_sink=None, run_log_path=None, turn_id=None, approval_callback=None):
    """One living L4 builds the task; one living L3 reviews it across rounds.

    The TDD harness runs first (tests only, red gate, hash lock), then
    implementation. L3 NEEDS_WORK sends feedback back to the same L4; the loop
    repeats until L3 passes, or a turn/time limit is reached. A passing task is
    committed in git; a failed attempt reverts workspace changes to the pre-task
    snapshot.
    """
    from langbridge_cli.agents.multi_agent import (
        l3_review_passed,
        l4_ready_for_review,
        new_l3_session,
        new_l4_session,
        run_l3_test_engineer,
        run_l4_engineer,
    )
    from langbridge_cli.agents import tdd_harness, workspace_git

    task = arguments.get("task", "")
    context = arguments.get("context", "")
    feedback = arguments.get("feedback", "")

    snapshot = workspace_git.snapshot_head()
    early, impl_session, l4_report, acceptance = _run_worker_tdd(
        api_key,
        model,
        task,
        context,
        feedback,
        new_l4_session,
        run_l4_engineer,
        l4_ready_for_review,
        "L4",
        trace_sink,
        approval_callback,
        run_log_path,
        turn_id,
    )
    if early is not None:
        workspace_git.revert_snapshot(snapshot)
        return early

    ok, gate_msg = _verify_acceptance_if_present(acceptance)
    if not ok:
        workspace_git.revert_snapshot(snapshot)
        return pm_review_result(l4_report, gate_msg, "NEEDS_WORK")

    start_worklog(run_log_path, task)
    append_worklog_entry(run_log_path, "L4 engineer", l4_report, "ready")

    l3 = new_l3_session(api_key, model, trace_sink=trace_sink, run_log_path=run_log_path, turn_id=turn_id)
    l3_report = ""
    start_time = now()
    for _ in range(MAX_L4_L3_TURNS):
        if over_time_budget(start_time, MAX_L4_L3_SECONDS):
            break
        ok, gate_msg = _verify_acceptance_if_present(acceptance)
        if not ok:
            append_worklog_entry(run_log_path, "TDD harness", gate_msg, "failure")
            workspace_git.revert_snapshot(snapshot)
            return pm_review_result(l4_report, gate_msg, "NEEDS_WORK")
        l3_report = run_l3_test_engineer(
            api_key,
            model,
            task,
            pm_l3_review_context(context, l4_report, acceptance=acceptance),
            session=l3,
        )
        if l3_review_passed(l3_report):
            ok, gate_msg = _verify_acceptance_if_present(acceptance)
            if ok:
                append_worklog_entry(run_log_path, "L3 test engineer", l3_report, "pass")
                workspace_git.commit_task("L4", task)
                return pm_review_result(l4_report, l3_report, "OK")
            append_worklog_entry(run_log_path, "Acceptance gate", gate_msg, "failure")
            l3_report = (
                "L3_STATUS: NEEDS_WORK\n"
                "Tests: acceptance gate failed after L3 PASS\n"
                f"Summary: {gate_msg}"
            )
        else:
            append_worklog_entry(run_log_path, "L3 test engineer", l3_report, "concern exist")

        l4_report = run_l4_engineer(
            api_key,
            model,
            task,
            context,
            l3_report,
            session=impl_session,
            user_prompt=tdd_harness.implement_phase_user_prompt(task, context, acceptance, l3_report, "L4"),
        )
        if not l4_ready_for_review(l4_report):
            append_worklog_entry(run_log_path, "L4 engineer", l4_report, "needs pm")
            workspace_git.revert_snapshot(snapshot)
            return pm_review_result(l4_report, l3_report, "NEEDS_WORK")
        append_worklog_entry(run_log_path, "L4 engineer", l4_report, "ready")

    workspace_git.revert_snapshot(snapshot)
    return pm_review_result(l4_report, l3_report, "NEEDS_WORK")


def _run_worker_tdd(
    api_key,
    model,
    task,
    context,
    feedback,
    new_session_fn,
    run_engineer_fn,
    ready_fn,
    worker_label,
    trace_sink,
    approval_callback,
    run_log_path,
    turn_id,
):
    """Return (early_report, impl_session, impl_report, acceptance_spec).

    When early_report is not None the caller should return it immediately.
    acceptance_spec is the frozen test.json dict (empty when TDD is bypassed).
    """
    from langbridge_cli.agents import tdd_harness

    session_kwargs = dict(
        trace_sink=trace_sink,
        approval_callback=approval_callback,
        run_log_path=run_log_path,
        turn_id=turn_id,
    )
    test_session = new_session_fn(api_key, model, write_guard=tdd_harness.test_phase_guard, **session_kwargs)
    test_report = run_engineer_fn(
        api_key,
        model,
        task,
        context,
        session=test_session,
        user_prompt=tdd_harness.test_phase_user_prompt(task, context, worker_label),
    )
    if not ready_fn(test_report):
        return test_report, None, None, None

    test_paths = tdd_harness.collect_changed_test_paths()
    red_ok, red_msg = tdd_harness.verify_red_gate(test_paths)
    if not red_ok:
        return f"{worker_label}_STATUS: IN_PROGRESS\nSummary: {red_msg}", None, None, None

    locked = tdd_harness.lock_hashes(test_paths)
    acceptance = tdd_harness.write_test_json(task, locked)
    write_guard = lambda tool, args: tdd_harness.implement_phase_guard(locked, tool, args)
    impl_session = new_session_fn(api_key, model, write_guard=write_guard, **session_kwargs)
    impl_report = run_engineer_fn(
        api_key,
        model,
        task,
        context,
        feedback,
        session=impl_session,
        user_prompt=tdd_harness.implement_phase_user_prompt(task, context, acceptance, feedback, worker_label),
    )
    if not ready_fn(impl_report):
        return impl_report, None, None, None
    green_ok, green_msg = tdd_harness.verify_green_gate(acceptance)
    if not green_ok:
        return f"{worker_label}_STATUS: IN_PROGRESS\nSummary: {green_msg}", None, None, None
    return None, impl_session, impl_report, acceptance


def _verify_acceptance_if_present(acceptance: dict) -> tuple[bool, str]:
    from langbridge_cli.agents import tdd_harness

    if not acceptance.get("paths"):
        return True, ""
    return tdd_harness.verify_acceptance(acceptance)


def pm_review_result(l4_report, l3_report, pm_status):
    return f"{l4_report}\n\nPM_DETERMINISTIC_L3_REVIEW:\n{l3_report}\n\nPM_REVIEW_STATUS: {pm_status}"


def run_l5_component(api_key, model, arguments, trace_sink=None, run_log_path=None, turn_id=None, approval_callback=None):
    """The L5 Ralph loop for one HARD component_task.

    L5 first writes (or reuses) a component_task_plan that splits the task into
    technical_sub_tasks, then conquers them one at a time. Each sub-task goes
    through the L5<->L3 review loop. A passed sub-task is committed in git and
    checked off in the plan; a failed attempt reverts workspace changes, splits
    the unfinished sub-task into smaller steps in the plan, and escalates to the
    PM so it can call L5 again later.
    """
    from langbridge_cli.agents.component_plan import (
        next_unfinished_index,
        parse_sub_tasks,
        read_component_plan,
        render_component_plan,
        replace_sub_task,
        write_component_plan,
    )
    from langbridge_cli.agents.multi_agent import l5_ready_for_review, new_l5_session, run_l5_engineer
    from langbridge_cli.agents import workspace_git

    task = arguments.get("task", "")
    context = arguments.get("context", "")
    start_worklog(run_log_path, f"L5 component: {task}", "L5")

    sub_tasks = parse_sub_tasks(read_component_plan(task))
    if not sub_tasks:
        plan_output = run_l5_call(api_key, model, l5_plan_prompt(task), context, "", trace_sink, run_log_path, turn_id, approval_callback)
        append_worklog_entry(run_log_path, "L5 engineer", plan_output, "plan", "L5")
        sub_tasks = parse_sub_tasks(plan_output) or [(task, False)]
        write_component_plan(task, render_component_plan(task, sub_tasks))

    for _ in range(MAX_L5_RALPH_TURNS):
        index = next_unfinished_index(sub_tasks)
        if index is None:
            return l5_component_result(task, sub_tasks, "OK", "All technical_sub_tasks passed L3 review.")
        sub_task = sub_tasks[index][0]
        sub_context = l5_sub_task_context(task, context)
        snapshot = workspace_git.snapshot_head()

        from langbridge_cli.agents.multi_agent import l5_ready_for_review, new_l5_session, run_l5_engineer

        early, l5, l5_output, acceptance = _run_worker_tdd(
            api_key,
            model,
            sub_task,
            sub_context,
            "",
            new_l5_session,
            run_l5_engineer,
            l5_ready_for_review,
            "L5",
            trace_sink,
            approval_callback,
            run_log_path,
            turn_id,
        )
        if early is not None:
            append_worklog_entry(run_log_path, "L5 engineer", early, "needs pm", "L5")
            return _l5_fail_sub_task(
                api_key, model, task, context, sub_tasks, index, sub_task, snapshot,
                f"L5 could not deliver technical_sub_task '{sub_task}':\n{early}",
                early,
                trace_sink, run_log_path, turn_id, approval_callback,
            )

        accepted, _, review = run_l5_review_loop(
            api_key, model, sub_task, sub_context, l5_output, l5, acceptance, trace_sink, run_log_path, turn_id, approval_callback
        )
        if not accepted:
            return _l5_fail_sub_task(
                api_key, model, task, context, sub_tasks, index, sub_task, snapshot,
                f"technical_sub_task '{sub_task}' failed L3 review; escalating to PM.\n{review}",
                review,
                trace_sink, run_log_path, turn_id, approval_callback,
            )

        workspace_git.commit_sub_task(sub_task)
        sub_tasks[index] = (sub_task, True)
        write_component_plan(task, render_component_plan(task, sub_tasks))

    return l5_component_result(task, sub_tasks, "NEEDS_WORK", "L5 hit the max Ralph turns before finishing every technical_sub_task.")


def _l5_fail_sub_task(
    api_key, model, component_task, context, sub_tasks, index, sub_task, snapshot,
    note, failure_detail, trace_sink, run_log_path, turn_id, approval_callback,
):
    from langbridge_cli.agents.component_plan import render_component_plan, write_component_plan
    from langbridge_cli.agents import workspace_git

    workspace_git.revert_snapshot(snapshot)
    sub_tasks = refine_failed_sub_task(
        api_key, model, component_task, sub_tasks, index, sub_task, failure_detail,
        trace_sink, run_log_path, turn_id, approval_callback,
    )
    write_component_plan(component_task, render_component_plan(component_task, sub_tasks))
    append_worklog_entry(run_log_path, "L5 planner", "Refined unfinished sub-task in component_task_plan.", "plan", "L5")
    return l5_component_result(
        component_task,
        sub_tasks,
        "NEEDS_WORK",
        f"{note}\n\nThe unfinished sub-task was split into smaller steps in the component_task_plan; call L5 again to continue.",
    )


def refine_failed_sub_task(
    api_key, model, component_task, sub_tasks, index, failed_sub_task, reason,
    trace_sink, run_log_path, turn_id, approval_callback,
):
    from langbridge_cli.agents.component_plan import parse_sub_tasks, replace_sub_task

    prompt = (
        "Refine only. The technical_sub_task below was too large or failed review. "
        "Split it into 2-4 smaller, ordered steps that can each be implemented and "
        "tested on its own. Return ONLY the checklist, one item per line as "
        "'- [ ] <technical_sub_task>'.\n\n"
        f"Component task:\n{component_task}\n\n"
        f"Failed sub-task:\n{failed_sub_task}\n\n"
        f"What went wrong:\n{reason[:2000]}"
    )
    output = run_l5_call(api_key, model, prompt, "", "", trace_sink, run_log_path, turn_id, approval_callback)
    new_items = [text for text, _ in parse_sub_tasks(output)]
    if not new_items:
        new_items = [
            f"First part: {failed_sub_task}",
            f"Finish and verify: {failed_sub_task}",
        ]
    return replace_sub_task(sub_tasks, index, new_items)


def run_l5_review_loop(api_key, model, sub_task, context, l5_output, l5, acceptance, trace_sink, run_log_path, turn_id, approval_callback):
    """One living L5 and one living L3 trade review turns until the sub-task passes or limits trip."""
    from langbridge_cli.agents import tdd_harness
    from langbridge_cli.agents.multi_agent import (
        l3_review_passed,
        l5_ready_for_review,
        new_l3_session,
        run_l3_test_engineer,
        run_l5_engineer,
    )

    l5_report = l5_output
    append_worklog_entry(run_log_path, "L5 engineer", l5_report, "ready", "L5")

    l3 = new_l3_session(api_key, model, trace_sink=trace_sink, run_log_path=run_log_path, turn_id=turn_id)
    l3_report = ""
    start_time = now()
    for _ in range(MAX_L4_L3_TURNS):
        if over_time_budget(start_time, MAX_L4_L3_SECONDS):
            break
        ok, gate_msg = _verify_acceptance_if_present(acceptance)
        if not ok:
            append_worklog_entry(run_log_path, "TDD harness", gate_msg, "failure", "L5")
            return False, l5_report, gate_msg
        l3_report = run_l3_test_engineer(
            api_key,
            model,
            sub_task,
            pm_l3_review_context(context, l5_report, "L5", acceptance=acceptance),
            session=l3,
        )
        if l3_review_passed(l3_report):
            ok, gate_msg = _verify_acceptance_if_present(acceptance)
            if ok:
                append_worklog_entry(run_log_path, "L3 test engineer", l3_report, "pass", "L5")
                return True, l5_report, l3_report
            append_worklog_entry(run_log_path, "Acceptance gate", gate_msg, "failure", "L5")
            l3_report = (
                "L3_STATUS: NEEDS_WORK\n"
                "Tests: acceptance gate failed after L3 PASS\n"
                f"Summary: {gate_msg}"
            )
        else:
            append_worklog_entry(run_log_path, "L3 test engineer", l3_report, "concern exist", "L5")

        l5_report = run_l5_engineer(
            api_key,
            model,
            sub_task,
            context,
            l3_report,
            session=l5,
            user_prompt=tdd_harness.implement_phase_user_prompt(sub_task, context, acceptance, l3_report, "L5"),
        )
        if not l5_ready_for_review(l5_report):
            append_worklog_entry(run_log_path, "L5 engineer", l5_report, "needs pm", "L5")
            return False, l5_report, l3_report
        append_worklog_entry(run_log_path, "L5 engineer", l5_report, "ready", "L5")

    return False, l5_report, l3_report


def run_l5_call(api_key, model, task, context, feedback, trace_sink, run_log_path, turn_id, approval_callback):
    from langbridge_cli.agents.multi_agent import run_l5_engineer

    if trace_sink is None and run_log_path is None and approval_callback is None:
        return run_l5_engineer(api_key, model, task, context, feedback)
    return run_l5_engineer(
        api_key,
        model,
        task,
        context,
        feedback,
        trace_sink=trace_sink,
        approval_callback=approval_callback,
        run_log_path=run_log_path,
        turn_id=turn_id,
    )


def l5_plan_prompt(task):
    return (
        "Plan only. Break this HARD component_task into a short, ordered checklist of "
        "technical_sub_tasks, each small enough to implement and test on its own. The "
        "last item MUST be an integration test for the whole component_task. Return ONLY "
        "the checklist, one item per line as '- [ ] <technical_sub_task>'.\n\n"
        f"Component task:\n{task}"
    )


def l5_sub_task_context(task, context):
    parts = [f"This is one technical_sub_task of the larger HARD component_task:\n{task}"]
    if context:
        parts.append(context)
    return "\n\n".join(parts)


def l5_component_result(task, sub_tasks, status, note):
    done = sum(1 for _, finished in sub_tasks if finished)
    summary = "\n".join(
        [
            f"L5_COMPONENT_DELIVERY: {task}",
            f"Technical sub-tasks complete: {done}/{len(sub_tasks)}.",
            note,
        ]
    )
    return f"{summary}\n\nPM_REVIEW_STATUS: {status}"


def pm_l3_review_context(context, worker_output, worker_label="L4", acceptance=None):
    from langbridge_cli.agents import tdd_harness

    parts = []
    if acceptance and acceptance.get("paths"):
        parts.append(tdd_harness.acceptance_context_block(acceptance))
    if context:
        parts.append(context)
    parts.append(f"{worker_label} completed work and is ready for PM-triggered L3 review.")
    parts.append(f"{worker_label} report:\n{worker_output}")
    return "\n\n".join(parts)


def add_hidden_tool_context(function, arguments, api_key, model, trace_sink=None, approval_callback=None, run_log_path=None, turn_id=None):
    parameters = inspect.signature(function).parameters
    tool_arguments = dict(arguments)
    if "api_key" in parameters:
        tool_arguments["api_key"] = api_key
    if "model" in parameters:
        tool_arguments["model"] = model
    if "trace_sink" in parameters:
        tool_arguments["trace_sink"] = trace_sink
    if "approval_callback" in parameters:
        tool_arguments["approval_callback"] = approval_callback
    if "run_log_path" in parameters:
        tool_arguments["run_log_path"] = run_log_path
    if "turn_id" in parameters:
        tool_arguments["turn_id"] = turn_id
    return tool_arguments


def approve_write_tool(name, arguments, approval_callback=None):
    if approval_callback is not None:
        return approval_callback("PM agent", name, arguments)
    if not sys.stdin.isatty():
        return False

    print(f"\nApprove write tool: {name}")
    print(json.dumps(arguments, ensure_ascii=False, indent=2))
    answer = input("Run this tool? [y/N] ")
    if answer.strip().lower() in {"y", "yes"}:
        return True
    # Denying at the prompt aborts the whole turn and returns to the REPL,
    # rather than feeding a "not approved" tool error back to the model.
    raise control.TurnAborted(f"{name} was denied.")
