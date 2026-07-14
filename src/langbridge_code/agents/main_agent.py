"""Main LangBridge agent: persistent session with full tools and subagent delegation."""
import json

from langbridge_code.agents.common import control
from langbridge_code.agents.common.parallel_tools import run_tool_calls
from langbridge_code.agents.common.limits import now, over_time_budget
from langbridge_code.llm.client import create_model_response
from langbridge_code.agents.system_prompt import langbridge_system_prompt
from langbridge_code.llm.parse import extract_output_text, print_step_trace
from langbridge_code.tools.common.purpose import without_purpose
from langbridge_code.util.agent_worklog import (
    write_worklog_finish,
    write_worklog_observation,
    write_worklog_received,
    write_worklog_step,
)
from langbridge_code.context.common.budget import prepare_agent_messages
from langbridge_code.context.agent_context import finish_step, init_agent_context
from langbridge_code.context.foreground import ForegroundTracker
from langbridge_code.util.progress import (
    PROGRESS_HEADER,
    build_turn_user_content,
    finalize_main_agent_turn,
    read_progress,
)
from langbridge_code.util.artifacts import format_trace_timestamp
from langbridge_code.util.trace_log import (
    begin_trace,
    combine_trace_sink,
    end_trace,
    trace_sink as write_trace_event,
)
from langbridge_code.settings import (
    MAX_AGENT_SECONDS,
    MAX_AGENT_STEPS,
    PROGRESS_NOTE_REMINDER_ROUNDS,
)
from langbridge_code.tools import MAIN_TOOL_SCHEMAS, MAIN_TOOLS
from langbridge_code.tools.ask_user import ASK_USER_TOOL_SCHEMA, resolve_ask_user
from langbridge_code.tools.note_progress import NOTE_FORK_INSTRUCTION, NOTE_PROGRESS_TOOL_SCHEMA
from langbridge_code.tools.remember import REMEMBER_TOOL_SCHEMA
from langbridge_code.memory import write_memory
from langbridge_code.agents.common.phases import emit_phase
from langbridge_code.agents.goal_evaluator import GoalEvaluatorAgent
from langbridge_code.util.goal import (
    STATUS_ACHIEVED,
    STATUS_PAUSED,
    SessionGoal,
    build_continuation_prompt,
    goal_turn_limit_reached,
    save_goal,
)
from langbridge_code.tools.agent_planner import AGENT_PLANNER_TOOL_SCHEMA, build_agent_planner_tool
from langbridge_code.tools.agent_worker_reviewer import AGENT_WORKER_TOOL_SCHEMA, build_agent_worker_tool
from langbridge_code.tools.agent_explorer import AGENT_EXPLORER_TOOL_SCHEMA, build_agent_explorer_tool

SUBAGENT_TOOL_SCHEMAS = [
    AGENT_PLANNER_TOOL_SCHEMA,
    AGENT_WORKER_TOOL_SCHEMA,
    AGENT_EXPLORER_TOOL_SCHEMA,
]

MAIN_AGENT_TOOL_SCHEMAS = (
    list(MAIN_TOOL_SCHEMAS)
    + [ASK_USER_TOOL_SCHEMA, NOTE_PROGRESS_TOOL_SCHEMA, REMEMBER_TOOL_SCHEMA]
    + list(SUBAGENT_TOOL_SCHEMAS)
)

PROGRESS_NOTE_REMINDER = (
    "[HOOK] More than {rounds} rounds have passed without a progress note. "
    "If anything meaningful was completed or decided since the last note, call "
    "note_progress in your next step (it forks a note-writer that summarizes "
    "the work since the last note); otherwise keep going."
)


def ensure_langbridge_system_prompt(messages):
    prompt = langbridge_system_prompt()
    if not messages:
        return [{"role": "system", "content": prompt}]
    if messages[0].get("role") == "system":
        messages[0]["content"] = prompt
        return messages
    return [{"role": "system", "content": prompt}, *messages]


class MainAgentSession:
    """Main agent for one chat session; message history persists across user turns."""

    def __init__(
        self,
        api_key,
        model,
        messages,
        run_log_path,
        turn_id,
        *,
        target="",
        trace_sink=None,
        approval_callback=None,
        phase_sink=None,
        question_callback=None,
        history_briefing_pending=True,
    ):
        self.api_key = api_key
        self.model = model
        self.run_log_path = run_log_path
        self.turn_id = turn_id
        self.target = target
        self.trace_sink = trace_sink
        self.approval_callback = approval_callback
        self.phase_sink = phase_sink
        self.question_callback = question_callback
        self.label = "LangBridge"
        self.step = 0
        self._rounds_since_progress_note = 0
        # <memory>/<progress>/<skill_index> blocks are prefetched on first send.
        del history_briefing_pending  # superseded by the pinned context blocks
        self._context_blocks_ready = False
        seed = ensure_langbridge_system_prompt(messages)
        self._system_prompt = (
            seed[0]["content"]
            if seed and seed[0].get("role") == "system"
            else langbridge_system_prompt()
        )
        self.messages, self.context, self.worklog_id = init_agent_context(
            system_prompt=self._system_prompt,
            run_log_path=run_log_path,
            label=self.label,
            seed_messages=seed,
        )
        self.tools = {**MAIN_TOOLS}
        self._rebuild_subagent_tools()

    def bind_turn(
        self,
        turn_id,
        *,
        target="",
        run_log_path=None,
        trace_sink=None,
        approval_callback=None,
        phase_sink=None,
        question_callback=None,
    ):
        """Point this session at the next user turn without resetting messages."""
        self.turn_id = turn_id
        self.target = target
        if run_log_path is not None:
            self.run_log_path = run_log_path
        if trace_sink is not None:
            self.trace_sink = trace_sink
        if approval_callback is not None:
            self.approval_callback = approval_callback
        if phase_sink is not None:
            self.phase_sink = phase_sink
        if question_callback is not None:
            self.question_callback = question_callback
        self._rebuild_subagent_tools()

    def _rebuild_subagent_tools(self):
        self.tools.update({
            "agent_planner": build_agent_planner_tool(
                api_key=self.api_key,
                model=self.model,
                run_log_path=self.run_log_path,
                turn_id=self.turn_id,
                trace_sink=self.trace_sink,
                phase_sink=self.phase_sink,
                question_callback=self.question_callback,
            ),
            "agent_worker": build_agent_worker_tool(
                api_key=self.api_key,
                model=self.model,
                run_log_path=self.run_log_path,
                turn_id=self.turn_id,
                messages=self.messages,
                target=self.target,
                trace_sink=self.trace_sink,
                phase_sink=self.phase_sink,
                approval_callback=self.approval_callback,
                question_callback=self.question_callback,
            ),
            "agent_explorer": build_agent_explorer_tool(
                api_key=self.api_key,
                model=self.model,
                run_log_path=self.run_log_path,
                turn_id=self.turn_id,
                trace_sink=self.trace_sink,
                phase_sink=self.phase_sink,
            ),
        })

    def run_turn(self, user_prompt, *, print_reply=False):
        """Run one user turn: agent loop and assistant reply."""
        reply = self.send(user_prompt)
        emit_phase(self.phase_sink, "summarizing")
        if print_reply:
            print(f"\n{reply}\n")
        return reply

    def run_goal_loop(
        self,
        goal: SessionGoal,
        *,
        initial_prompt: str | None = None,
        eval_model: str | None = None,
        on_round=None,
        on_verdict=None,
    ) -> tuple[str, SessionGoal]:
        """Run LangBridge rounds until the evaluator confirms the goal or limits hit."""
        evaluator = GoalEvaluatorAgent(
            self.api_key,
            eval_model or self.model,
            run_log_path=self.run_log_path,
            trace_sink=self.trace_sink,
        )
        prompt = (initial_prompt or goal.condition).strip()
        if not prompt:
            prompt = goal.condition
        last_reply = ""

        while goal.active:
            control.checkpoint()
            if goal_turn_limit_reached(goal):
                goal.status = STATUS_PAUSED
                goal.last_reason = f"Reached turn limit ({goal.max_turns})."
                save_goal(self.run_log_path, goal)
                break

            reply = self.send(prompt)
            last_reply = reply
            goal.turn_count += 1
            save_goal(self.run_log_path, goal)
            if on_round:
                on_round(reply)

            emit_phase(self.phase_sink, "evaluating")
            verdict = evaluator.evaluate(goal.condition, self.messages)
            goal.last_reason = verdict.reason
            goal.last_guidance = verdict.guidance
            save_goal(self.run_log_path, goal)
            if on_verdict:
                on_verdict(verdict)

            if verdict.met:
                goal.status = STATUS_ACHIEVED
                save_goal(self.run_log_path, goal)
                break

            if goal_turn_limit_reached(goal):
                goal.status = STATUS_PAUSED
                save_goal(self.run_log_path, goal)
                break

            prompt = build_continuation_prompt(goal)

        emit_phase(self.phase_sink, "summarizing")
        return last_reply, goal

    def _refresh_memory_and_progress_blocks(self, task=""):
        """Prefetch <memory> (one LLM pass over memory.md) and re-read <progress>.

        Runs on the first send and again after every compaction — the head of
        the context is dropped and rebuilt from possibly-updated memory, and
        progress.md is re-concatenated.
        """
        from langbridge_code.memory import prefetch_memory

        stack = self.context.stack
        stack.set_memory_block(
            prefetch_memory(self.api_key, self.model, task or self.target or "")
        )
        progress = read_progress(self.run_log_path).strip()
        if progress == PROGRESS_HEADER.strip():
            progress = ""
        stack.set_progress_block(progress)

    def _init_context_blocks(self, user_prompt):
        """First-send prefetch: <memory> + <progress> + <skill_index>."""
        from langbridge_code.skills import ensure_skill_index_block, langbridge_skill_catalog

        self._refresh_memory_and_progress_blocks(task=user_prompt)
        ensure_skill_index_block(
            self.context.stack,
            self.api_key,
            self.model,
            user_prompt,
            langbridge_skill_catalog(),
            label="LangBridge skill prefetch",
        )
        self.context.stack.on_compacted = (
            lambda _stack: self._refresh_memory_and_progress_blocks()
        )
        self._context_blocks_ready = True

    def send(self, user_prompt):
        if not self._context_blocks_ready:
            self._init_context_blocks(user_prompt)
        turn_content = build_turn_user_content(self.run_log_path, user_prompt)
        self.context.begin_turn(turn_content)
        write_worklog_received(self.run_log_path, self.label, self.worklog_id, self.turn_id, user_prompt)
        foreground = ForegroundTracker(self.label, self.messages, self.model)
        foreground.activate()
        start_time = now()
        try:
            for _ in range(MAX_AGENT_STEPS):
                control.checkpoint()
                if over_time_budget(start_time, MAX_AGENT_SECONDS):
                    return self._finish(f"{self.label} stopped: out of time.")
                self.context.compact_to_budget(api_key=self.api_key, model=self.model)
                budget = prepare_agent_messages(
                    self.messages,
                    self.model,
                    base_system_prompt=self._system_prompt,
                )
                foreground.publish()
                response = control.run_interruptible(
                    lambda: create_model_response(
                        self.api_key,
                        self.model,
                        self.messages,
                        tool_schemas=MAIN_AGENT_TOOL_SCHEMAS,
                        reasoning={"summary": "auto"},
                        label=self.label,
                        stream_sink=self.trace_sink,
                    )
                )
                output = response.get("output", [])
                tool_calls = [item for item in output if item.get("type") == "function_call"]
                if not tool_calls:
                    print_step_trace(output, include_message=True, label=self.label, sink=self.trace_sink)
                    if output:
                        finish_step(self.context, list(output), self, budget)
                        foreground.publish()
                    return self._finish(extract_output_text(output))
                print_step_trace(output, include_message=True, label=self.label, sink=self.trace_sink)
                write_worklog_step(self.run_log_path, self.label, self.worklog_id, self.turn_id, self.step, output)
                step_items = list(output)
                tool_outputs = run_tool_calls(self._run_tool, tool_calls)
                for tool_output in tool_outputs:
                    step_items.append(tool_output)
                    write_worklog_observation(
                        self.run_log_path, self.label, self.worklog_id, self.turn_id, self.step, tool_output
                    )
                self.step += 1
                finish_step(self.context, step_items, self, budget)
                self._maybe_remind_progress_note()
                foreground.publish()
            return self._finish(f"{self.label} stopped: max steps.")
        finally:
            foreground.deactivate()

    def _maybe_remind_progress_note(self):
        """Nudge the agent to note_progress after too many silent rounds."""
        self._rounds_since_progress_note += 1
        if self._rounds_since_progress_note <= PROGRESS_NOTE_REMINDER_ROUNDS:
            return
        self._rounds_since_progress_note = 0
        self.context.begin_turn(
            PROGRESS_NOTE_REMINDER.format(rounds=PROGRESS_NOTE_REMINDER_ROUNDS)
        )

    def _write_progress_note_via_fork(self):
        """Fork a one-pass note-writer on the live context (prefix cache) and
        append its summary to progress.md. Compress progress.md if it outgrows
        its share of the context window."""
        from langbridge_code.agents.common.fork import fork_one_pass
        from langbridge_code.util.progress import append_progress_note, maybe_compact_progress

        try:
            note = fork_one_pass(
                self.api_key,
                self.model,
                self.messages,
                NOTE_FORK_INSTRUCTION,
                label="progress note fork",
            )
        except Exception as error:
            return f"Progress note fork failed: {error}"
        if not note.strip():
            return "Progress note fork returned nothing; no note recorded."
        result = append_progress_note(self.run_log_path, self.turn_id, note)
        maybe_compact_progress(self.api_key, self.model, self.run_log_path)
        return result

    def _run_tool(self, call):
        name = call.get("name")
        call_id = call.get("call_id")
        try:
            arguments = without_purpose(json.loads(call.get("arguments") or "{}"))
            if name == "ask_user":
                output = resolve_ask_user(arguments, self.question_callback)
            elif name == "note_progress":
                output = self._write_progress_note_via_fork()
                self._rounds_since_progress_note = 0
            elif name == "remember":
                output = write_memory(
                    arguments.get("scope", ""),
                    arguments.get("title", ""),
                    arguments.get("content", ""),
                )
            elif name not in self.tools:
                raise ValueError(f"Unknown {self.label} tool: {name}")
            else:
                if name in {"read_plan", "clear_plan", "update_plan", "merge_branch"}:
                    arguments["run_log_path"] = self.run_log_path
                output = self.tools[name](**arguments)
        except Exception as error:
            output = f"Tool error: {error}"
        return {"type": "function_call_output", "call_id": call_id, "output": output}

    def _finish(self, report):
        from langbridge_code.memory import schedule_memory_extraction

        write_worklog_finish(self.run_log_path, self.label, self.worklog_id, self.turn_id, report)
        # Fork the memory-writer agent on the live context: reviews this turn
        # in the background, writes any missed durable memories, then exits.
        schedule_memory_extraction(self.api_key, self.model, self.messages)
        return report


def run_agent_turn(
    api_key,
    model,
    target,
    run_log_path,
    turn_id,
    trace_sink=None,
    print_reply=True,
    approval_callback=None,
    phase_sink=None,
    messages=None,
    question_callback=None,
):
    """Run one user turn through the main LangBridge agent."""
    if messages is None:
        messages = [{"role": "system", "content": langbridge_system_prompt()}]

    trace_id = format_trace_timestamp()
    begin_trace(run_log_path, trace_id)
    sink = combine_trace_sink(trace_sink, write_trace_event)

    outcome = ""
    try:
        session = MainAgentSession(
            api_key,
            model,
            messages,
            run_log_path,
            turn_id,
            target=target,
            trace_sink=sink,
            approval_callback=approval_callback,
            phase_sink=phase_sink,
            question_callback=question_callback,
        )
        reply = session.run_turn(target, print_reply=print_reply)
        outcome = reply or ""
    except control.StopRequested:
        outcome = "Stopped by user."
    except Exception as error:
        from langbridge_code.llm.client import format_api_error

        outcome = format_api_error(error)
    finally:
        end_trace()
        finalize_main_agent_turn(
            api_key,
            model,
            run_log_path,
            turn_id,
            user=target,
            assistant=outcome,
        )
    return outcome
