import copy
import inspect
import json
import sys

from openai import OpenAI, OpenAIError

from langbridge_cli.config import (
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
from langbridge_cli.llm.debug import print_llm_request, print_llm_response
from langbridge_cli.agents.roles import SYSTEM_PROMPT
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
    write_worklog_finish,
    write_worklog_observation,
    write_worklog_step,
)
from langbridge_cli.persistence.worklog import append_worklog_entry, start_worklog
from langbridge_cli.agents.limits import now, over_context_budget, over_time_budget
from langbridge_cli.agents import control


def run_pm_loop(
    api_key,
    model,
    target,
    run_log_path,
    turn_id,
    trace_sink=None,
    print_reply=True,
    approval_callback=None,
):
    finished = ""
    start_time = now()
    for _ in range(MAX_PM_LOOPS):
        if over_time_budget(start_time, MAX_PM_SECONDS):
            break
        round_input = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": pm_round_prompt(target, read_todo_list())},
        ]
        finished = run_agent(
            api_key,
            model,
            round_input,
            run_log_path,
            turn_id,
            trace_sink=trace_sink,
            print_reply=print_reply,
            approval_callback=approval_callback,
        )
        if not pm_should_continue(finished):
            break
    return finished


def pm_round_prompt(target, todo_list):
    parts = [f"Task from the user:\n{target}"]
    if todo_list:
        parts.append(f"Current todo_list:\n{todo_list}")
    else:
        parts.append("There is no todo_list yet.")
    return "\n\n".join(parts)


def pm_should_continue(finished):
    for line in reversed(finished.strip().splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped.upper() == "BUG_STATUS: OPEN"
    return False


def run_agent(
    api_key,
    model,
    input,
    run_log_path,
    turn_id,
    trace_sink=None,
    print_reply=True,
    approval_callback=None,
):
    write_input_log(run_log_path, turn_id, input) # write current message into log
    start_time = now()
    for step in range(MAX_AGENT_STEPS):
        control.checkpoint()
        if over_time_budget(start_time, MAX_AGENT_SECONDS):
            return finish_pm(input, "Agent stopped because it ran out of time.", run_log_path, turn_id, print_reply)
        if over_context_budget(input, MAX_AGENT_CONTEXT_TOKENS):
            return finish_pm(input, "Agent stopped because it exceeded the context budget.", run_log_path, turn_id, print_reply)
        step_response = control.run_interruptible(lambda: create_response(api_key, model, input)).get("output", [])
        tool_calls = [item for item in step_response if item.get("type") == "function_call"]
        print_step_trace(step_response, include_message=bool(tool_calls), label="PM agent", sink=trace_sink)

        if tool_calls:
            input.extend(step_response)
            write_tool_calls_log(run_log_path, turn_id, step, step_response) # write step_response or socalled "action" into log
            write_worklog_step(run_log_path, "PM agent", turn_id, step, step_response)
            for call in tool_calls:
                tool_output = run_tool_call(call, api_key, model, trace_sink, approval_callback, run_log_path, turn_id)
                input.append(tool_output)
                write_tool_calls_result_log(run_log_path, turn_id, step, tool_output) # write tool_output or socalled "observation" into log
                write_worklog_observation(run_log_path, "PM agent", turn_id, step, tool_output)
        else:
            return finish_pm(input, extract_output_text(step_response), run_log_path, turn_id, print_reply)
    return finish_pm(input, "Agent stopped because it reached the maximum tool-call steps.", run_log_path, turn_id, print_reply)


def finish_pm(input, finished, run_log_path, turn_id, print_reply):
    input.append({"role": "assistant", "content": finished})
    write_finish_log(run_log_path, turn_id, finished)
    write_worklog_finish(run_log_path, "PM agent", turn_id, finished)
    if print_reply:
        print(f"\n{finished}\n")
    return finished


def create_response(api_key, model, agent_input):
    client = OpenAI(api_key=api_key)
    print_llm_request("PM agent", model, agent_input, MAIN_TOOL_SCHEMAS)
    try:
        response = client.responses.create(
            model=model,
            input=agent_input,
            tools=MAIN_TOOL_SCHEMAS,
            reasoning={"summary": "auto"},
        )
    except OpenAIError as error:
        raise RuntimeError(str(error))

    data = response.model_dump(exclude_none=True)
    print_llm_response("PM agent", data)
    return data


def run_tool_call(call, api_key=None, model=None, trace_sink=None, approval_callback=None, run_log_path=None, turn_id=None):
    name = call.get("name")
    call_id = call.get("call_id")

    try:
        arguments = strip_tool_purpose(json.loads(call.get("arguments") or "{}"))
        if name not in MAIN_TOOLS:
            raise ValueError(f"Unknown tool: {name}")
        if name in WRITE_TOOLS and not approve_write_tool(name, arguments, approval_callback):
            raise PermissionError(f"{name} was not approved")
        tool_arguments = add_hidden_tool_context(MAIN_TOOLS[name], arguments, api_key, model, trace_sink, approval_callback, run_log_path, turn_id)
        output = MAIN_TOOLS[name](**tool_arguments)
        if name == "ask_l4_engineer":
            output = run_l4_component(api_key, model, arguments, trace_sink, run_log_path, turn_id, approval_callback)
        elif name == "ask_l5_engineer":
            output = run_l5_component(api_key, model, arguments, trace_sink, run_log_path, turn_id, approval_callback)
    except Exception as error:
        output = f"Tool error: {error}"

    return {"type": "function_call_output", "call_id": call_id, "output": output}


def run_l4_component(api_key, model, arguments, trace_sink=None, run_log_path=None, turn_id=None, approval_callback=None):
    """One living L4 builds the task; one living L3 reviews it across rounds.

    The L4 keeps its memory across the whole loop (its own tool calls/results and
    the L3 exchange), and so does the L3. Only the dispute jury is spawned fresh.
    The shared L4<->L3 worklog records every turn's status token, which is what the
    loop routes on to decide whether the task can end.
    """
    from langbridge_cli.agents.multi_agent import (
        l3_review_passed,
        l4_pushed_back,
        l4_ready_for_review,
        new_l3_session,
        new_l4_session,
        run_l3_test_engineer,
        run_l4_engineer,
    )

    task = arguments.get("task", "")
    context = arguments.get("context", "")
    feedback = arguments.get("feedback", "")

    l4 = new_l4_session(api_key, model, trace_sink=trace_sink, approval_callback=approval_callback, run_log_path=run_log_path, turn_id=turn_id)
    l4_report = run_l4_engineer(api_key, model, task, context, feedback, session=l4)
    if not l4_ready_for_review(l4_report):
        return l4_report

    start_worklog(run_log_path, task)
    append_worklog_entry(run_log_path, "L4 engineer", l4_report, "ready")

    l3 = new_l3_session(api_key, model, trace_sink=trace_sink, run_log_path=run_log_path, turn_id=turn_id)
    l3_report = ""
    start_time = now()
    for _ in range(MAX_L4_L3_TURNS):
        if over_time_budget(start_time, MAX_L4_L3_SECONDS):
            break
        l3_report = run_l3_test_engineer(api_key, model, task, pm_l3_review_context(context, l4_report), session=l3)
        if l3_review_passed(l3_report):
            append_worklog_entry(run_log_path, "L3 test engineer", l3_report, "pass")
            return pm_review_result(l4_report, l3_report, "OK")
        append_worklog_entry(run_log_path, "L3 test engineer", l3_report, "concern exist")

        disputed_impl = l4_report
        l4_response = run_l4_engineer(api_key, model, task, context, l3_report, session=l4)
        if l4_pushed_back(l4_response):
            append_worklog_entry(run_log_path, "L4 engineer", l4_response, "push back")
            return resolve_push_back(api_key, model, task, context, disputed_impl, l4_response, l3_report, l3, trace_sink, run_log_path, turn_id)
        l4_report = l4_response
        if not l4_ready_for_review(l4_report):
            append_worklog_entry(run_log_path, "L4 engineer", l4_report, "needs pm")
            return pm_review_result(l4_report, l3_report, "NEEDS_WORK")
        append_worklog_entry(run_log_path, "L4 engineer", l4_report, "ready")

    return pm_review_result(l4_report, l3_report, "NEEDS_WORK")


def resolve_push_back(api_key, model, task, context, disputed_impl, l4_push_back, prior_l3_report, l3, trace_sink, run_log_path, turn_id):
    """The same living L3 re-judges the push-back; if it still objects, a fresh 2-juror jury settles it."""
    from langbridge_cli.agents.multi_agent import l3_review_passed, run_l3_test_engineer

    rejudge = run_l3_test_engineer(
        api_key, model, task, push_back_rejudge_context(context, disputed_impl, l4_push_back, prior_l3_report), session=l3
    )
    if l3_review_passed(rejudge):
        append_worklog_entry(run_log_path, "L3 test engineer", rejudge, "pass")
        return pm_review_result(disputed_impl, rejudge, "OK")
    append_worklog_entry(run_log_path, "L3 test engineer", rejudge, "push back")

    jury_pass, jury_summary = run_dispute_jury(api_key, model, task, context, disputed_impl, trace_sink, run_log_path, turn_id)
    append_worklog_entry(run_log_path, "Dispute jury", jury_summary, "pass" if jury_pass else "failure")
    return pm_review_result(disputed_impl, jury_summary, "OK" if jury_pass else "NEEDS_WORK")


def run_dispute_jury(api_key, model, task, context, worker_report, trace_sink, run_log_path, turn_id, worker_label="L4"):
    from langbridge_cli.agents.multi_agent import l3_review_passed

    reports = [
        run_l3_review(api_key, model, task, juror_context(context, worker_report, worker_label), trace_sink, run_log_path, turn_id)
        for _ in range(2)
    ]
    jury_pass = all(l3_review_passed(report) for report in reports)
    return jury_pass, format_jury_summary(reports, jury_pass)


def push_back_rejudge_context(context, disputed_impl, push_back, prior_l3_report, worker_label="L4"):
    parts = []
    if context:
        parts.append(context)
    parts.append(f"{worker_label} pushed back on your review instead of changing the code.")
    parts.append(f"Your prior review:\n{prior_l3_report}")
    parts.append(f"{worker_label} implementation under review:\n{disputed_impl}")
    parts.append(f"{worker_label} push-back rationale:\n{push_back}")
    parts.append(
        "Re-judge honestly. If the push-back is right, concede and return PASS. "
        "If it is wrong, insist with NEEDS_WORK or FAIL; an independent jury will settle it."
    )
    return "\n\n".join(parts)


def juror_context(context, worker_report, worker_label="L4"):
    parts = []
    if context:
        parts.append(context)
    parts.append(f"You are an independent juror. Verify the {worker_label} implementation on its own merits and vote PASS or FAIL.")
    parts.append(f"{worker_label} implementation to verify:\n{worker_report}")
    return "\n\n".join(parts)


def format_jury_summary(reports, jury_pass):
    lines = [f"DISPUTE_JURY_RESULT: {'PASS' if jury_pass else 'FAIL'}", ""]
    for index, report in enumerate(reports, 1):
        lines.append(f"Juror {index}:\n{report}")
        lines.append("")
    return "\n".join(lines).strip()


def run_l3_review(api_key, model, task, l3_context, trace_sink, run_log_path, turn_id):
    from langbridge_cli.agents.multi_agent import run_l3_test_engineer

    if trace_sink is None and run_log_path is None:
        return run_l3_test_engineer(api_key, model, task, l3_context)
    return run_l3_test_engineer(
        api_key,
        model,
        task,
        l3_context,
        trace_sink=trace_sink,
        run_log_path=run_log_path,
        turn_id=turn_id,
    )


def pm_review_result(l4_report, l3_report, pm_status):
    return f"{l4_report}\n\nPM_DETERMINISTIC_L3_REVIEW:\n{l3_report}\n\nPM_REVIEW_STATUS: {pm_status}"


def run_l5_component(api_key, model, arguments, trace_sink=None, run_log_path=None, turn_id=None, approval_callback=None):
    """The L5 Ralph loop for one HARD component_task.

    L5 first writes (or reuses) a component_task_plan that splits the task into
    technical_sub_tasks, then conquers them one at a time. Each sub-task goes
    through the L5<->L3 review (with push-back and a 2-juror dispute), exactly like
    the L4 path. A passed sub-task is checked off in the plan; a failed one
    escalates to the PM. When every sub-task passes, the delivery returns to the PM
    to accept or reject, ending in PM_REVIEW_STATUS like the L4 path.
    """
    from langbridge_cli.agents.component_plan import (
        next_unfinished_index,
        parse_sub_tasks,
        read_component_plan,
        render_component_plan,
        write_component_plan,
    )
    from langbridge_cli.agents.multi_agent import l5_ready_for_review, new_l5_session, run_l5_engineer

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

        # A brand-new L5 owns this one sub-task; it stays alive across the sub-task's
        # review rounds but knows nothing of the L5s that handled earlier sub-tasks.
        l5 = new_l5_session(api_key, model, trace_sink=trace_sink, approval_callback=approval_callback, run_log_path=run_log_path, turn_id=turn_id)
        l5_output = run_l5_engineer(api_key, model, sub_task, sub_context, "", session=l5)
        if not l5_ready_for_review(l5_output):
            append_worklog_entry(run_log_path, "L5 engineer", l5_output, "needs pm", "L5")
            return l5_component_result(task, sub_tasks, "NEEDS_WORK", f"L5 could not deliver technical_sub_task '{sub_task}':\n{l5_output}")

        accepted, _, review = run_l5_review_loop(api_key, model, sub_task, sub_context, l5_output, l5, trace_sink, run_log_path, turn_id, approval_callback)
        if not accepted:
            return l5_component_result(task, sub_tasks, "NEEDS_WORK", f"technical_sub_task '{sub_task}' failed L3 review; escalating to PM.\n{review}")

        sub_tasks[index] = (sub_task, True)
        write_component_plan(task, render_component_plan(task, sub_tasks))

    return l5_component_result(task, sub_tasks, "NEEDS_WORK", "L5 hit the max Ralph turns before finishing every technical_sub_task.")


def run_l5_review_loop(api_key, model, sub_task, context, l5_output, l5, trace_sink, run_log_path, turn_id, approval_callback):
    """One living L5 (the same `l5` that built the sub-task) and one living L3 trade
    review turns until the sub-task passes, blocks, or a push-back is settled by a fresh jury."""
    from langbridge_cli.agents.multi_agent import (
        l3_review_passed,
        l5_pushed_back,
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
        l3_report = run_l3_test_engineer(api_key, model, sub_task, pm_l3_review_context(context, l5_report, "L5"), session=l3)
        if l3_review_passed(l3_report):
            append_worklog_entry(run_log_path, "L3 test engineer", l3_report, "pass", "L5")
            return True, l5_report, l3_report
        append_worklog_entry(run_log_path, "L3 test engineer", l3_report, "concern exist", "L5")

        disputed_impl = l5_report
        l5_response = run_l5_engineer(api_key, model, sub_task, context, l3_report, session=l5)
        if l5_pushed_back(l5_response):
            append_worklog_entry(run_log_path, "L5 engineer", l5_response, "push back", "L5")
            accepted, summary = resolve_l5_push_back(api_key, model, sub_task, context, disputed_impl, l5_response, l3_report, l3, trace_sink, run_log_path, turn_id)
            return accepted, disputed_impl, summary
        l5_report = l5_response
        if not l5_ready_for_review(l5_report):
            append_worklog_entry(run_log_path, "L5 engineer", l5_report, "needs pm", "L5")
            return False, l5_report, l3_report
        append_worklog_entry(run_log_path, "L5 engineer", l5_report, "ready", "L5")

    return False, l5_report, l3_report


def resolve_l5_push_back(api_key, model, sub_task, context, disputed_impl, l5_push_back, prior_l3_report, l3, trace_sink, run_log_path, turn_id):
    """The same living L3 re-judges the push-back; if it still objects, a fresh 2-juror jury settles it."""
    from langbridge_cli.agents.multi_agent import l3_review_passed, run_l3_test_engineer

    rejudge = run_l3_test_engineer(
        api_key, model, sub_task, push_back_rejudge_context(context, disputed_impl, l5_push_back, prior_l3_report, "L5"), session=l3
    )
    if l3_review_passed(rejudge):
        append_worklog_entry(run_log_path, "L3 test engineer", rejudge, "pass", "L5")
        return True, rejudge
    append_worklog_entry(run_log_path, "L3 test engineer", rejudge, "push back", "L5")

    jury_pass, jury_summary = run_dispute_jury(api_key, model, sub_task, context, disputed_impl, trace_sink, run_log_path, turn_id, "L5")
    append_worklog_entry(run_log_path, "Dispute jury", jury_summary, "pass" if jury_pass else "failure", "L5")
    return jury_pass, jury_summary


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


def pm_l3_review_context(context, worker_output, worker_label="L4"):
    parts = []
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
    return answer.strip().lower() in {"y", "yes"}
