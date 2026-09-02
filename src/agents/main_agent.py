"""Main LangBridge agent: persistent session with full tools and subagent delegation."""
import copy
import json

import threading

from langbridge_code.agents.common import control
from langbridge_code.agents.common import worktree as worktree_mod
from langbridge_code.agents.common.parallel_tools import (
    BACKGROUND_TOOL_NAMES,
    CompletionDrivenToolRunner,
    run_tool_calls,
)
from langbridge_code.agents.common.todo_list import artifact_plan_path, migrate_workspace_plan
from langbridge_code.agents.common.workspace import plan_file_scope
from langbridge_code.agents.common.limits import now, over_time_budget
from langbridge_code.llm.client import create_model_response, supports_native_tool_search
from langbridge_code.mcp_runtime import (
    CLIENT_TOOL_SEARCH_SCHEMA,
    TOOL_SEARCH_SCHEMA,
    RemoteMCPRegistry,
    format_tool_pool_reminder,
)
from langbridge_code.prompt.system import langbridge_system_prompt
from langbridge_code.schedules import obsidian_vault_path
from langbridge_code.llm.parse import extract_output_text, print_step_trace
from langbridge_code.tools.common.description import without_description
from langbridge_code.util.agent_worklog import (
    write_worklog_finish,
    write_worklog_observation,
    write_worklog_received,
)
from langbridge_code.context.common.budget import messages_with_budget_notice, prepare_agent_messages
from langbridge_code.context.agent_context import finish_step, init_agent_context
from langbridge_code.context.foreground import ForegroundTracker
from langbridge_code.util.progress import (
    SESSION_MEMORY_HEADER,
    build_turn_user_content,
    read_progress,
)
from langbridge_code.util.artifacts import format_trace_timestamp
from langbridge_code.util.session import (
    clear_fork_memory_context,
    read_fork_memory_context,
)
from langbridge_code.util.trace_log import (
    begin_trace,
    combine_trace_sink,
    end_trace,
    trace_sink as write_trace_event,
)
from langbridge_code.settings import (
    FINALIZE_RESERVE_SECONDS,
    GOAL_DEFAULT_MAX_TURNS,
    MAX_AGENT_SECONDS,
    MAX_AGENT_STEPS,
    SESSION_MEMORY_REMINDER_ROUNDS,
    model_for_agent,
)
from langbridge_code.tools import MAIN_TOOL_SCHEMAS, MAIN_TOOLS
from langbridge_code.agents.common.approval import (
    approval_callback_required,
    approval_reason,
)
from langbridge_code.tools.ask_user import ASK_USER_TOOL_SCHEMA, resolve_ask_user
from langbridge_code.tools.memory_writer import MEMORY_WRITER_TOOL_SCHEMA
from langbridge_code.tools.update_session_memory import UPDATE_SESSION_MEMORY_TOOL_SCHEMA
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
from langbridge_code.agents.planner import AGENT_PLANNER_TOOL_SCHEMA, build_agent_planner_tool
from langbridge_code.agents.worker_reviewer import AGENT_WORKER_TOOL_SCHEMA, build_agent_worker_tool
from langbridge_code.agents.explorer import AGENT_EXPLORER_TOOL_SCHEMA, build_agent_explorer_tool

SUBAGENT_TOOL_SCHEMAS = [
    AGENT_PLANNER_TOOL_SCHEMA,
    AGENT_WORKER_TOOL_SCHEMA,
    AGENT_EXPLORER_TOOL_SCHEMA,
]

MAIN_AGENT_TOOL_SCHEMAS = (
    list(MAIN_TOOL_SCHEMAS)
    + [ASK_USER_TOOL_SCHEMA, UPDATE_SESSION_MEMORY_TOOL_SCHEMA, MEMORY_WRITER_TOOL_SCHEMA]
    + list(SUBAGENT_TOOL_SCHEMAS)
)
SUBAGENT_TOOL_NAMES = frozenset(
    {"agent_planner", "agent_worker", "agent_explorer"}
)
FINALIZATION_TOOL_SCHEMAS = [
    schema
    for schema in MAIN_AGENT_TOOL_SCHEMAS
    if schema.get("name") not in SUBAGENT_TOOL_NAMES
]

FINALIZATION_NOTICE = (
    "[EVAL_DEADLINE]\n"
    "The run is in its finalization window. Do not start new planning, exploration, "
    "or worker tasks. Collect completed background results, merge only ready reviewed "
    "branches, preserve partial work, run only short targeted checks, and return the "
    "best implementation before the deadline."
)

BACKGROUND_PENDING = (
    "Background task started and is still running. Its real result will arrive "
    "later in a <background_tool_results> event. Do not merge or mark its todo "
    "complete from this placeholder."
)

PLAN_FILE_TOOL_NAMES = frozenset(
    {"read_file", "write", "Edit"}
)


def ensure_langbridge_system_prompt(messages):
    prompt = langbridge_system_prompt()
    if not messages:
        return [{"role": "system", "content": prompt}]
    if messages[0].get("role") == "system":
        messages[0]["content"] = prompt
        return messages
    return [{"role": "system", "content": prompt}, *messages]


def in_finalization_window(start_time: float) -> bool:
    if FINALIZE_RESERVE_SECONDS <= 0 or MAX_AGENT_SECONDS is None:
        return False
    threshold = max(0, MAX_AGENT_SECONDS - FINALIZE_RESERVE_SECONDS)
    return over_time_budget(start_time, threshold)


def request_messages(messages, model: str, *, finalizing: bool) -> list:
    request = messages_with_budget_notice(messages, model)
    if finalizing:
        request.append({"role": "user", "content": FINALIZATION_NOTICE})
    return request


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
        allowed_tool_names=None,
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
        self.allowed_tool_names = (
            frozenset(allowed_tool_names) if allowed_tool_names is not None else None
        )
        self.allowed_mcp_servers = (
            None
            if self.allowed_tool_names is None
            else frozenset(
                name.removeprefix("mcp:")
                for name in self.allowed_tool_names
                if name == "gmail" or name.startswith("mcp:")
            )
        )
        self.label = "LangBridge"
        self.step = 0
        self._rounds_since_session_memory = 0
        self._last_user_prompt = ""
        self._memory_writer_ran_this_send = False
        self._deadline_finalizing = False
        self._turn_start_time = None
        self._context_lock = threading.RLock()
        # <memory>/<session_memory>/<skill_index> blocks are prefetched on first send.
        del history_briefing_pending  # superseded by the pinned context blocks
        self._context_blocks_ready = False
        # Workers need worktrees from HEAD; bootstrap a repo before the system
        # prompt reports {is_git} and before any agent_worker dispatch.
        worktree_mod.ensure_git_repo()
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
        self.tools = {
            name: tool
            for name, tool in MAIN_TOOLS.items()
            if self.allowed_tool_names is None or name in self.allowed_tool_names
        }
        self.mcp_registry = RemoteMCPRegistry(
            role="langbridge",
            allowed_servers=self.allowed_mcp_servers,
        )
        allow_tool_search = (
            self.allowed_tool_names is None or bool(self.allowed_mcp_servers)
        )
        self._native_tool_search = allow_tool_search and supports_native_tool_search(
            self.model
        )
        self._native_tool_search_history: list[dict] = []
        session_schema_candidates = list(MAIN_AGENT_TOOL_SCHEMAS)
        if allow_tool_search:
            session_schema_candidates.append(
                CLIENT_TOOL_SEARCH_SCHEMA if self._native_tool_search else TOOL_SEARCH_SCHEMA
            )
        self._fixed_tool_schemas = [
            schema
            for schema in session_schema_candidates
            if self.allowed_tool_names is None
            or schema.get("name") in self.allowed_tool_names
            or (schema.get("name") == "ToolSearch" and allow_tool_search)
            or (schema.get("type") == "tool_search" and allow_tool_search)
        ]
        self._announced_mcp_names: tuple[str, ...] | None = None
        self._announced_obsidian_vault: str | None = None
        self.tool_schemas = []
        self.finalization_tool_schemas = []
        self._sync_deferred_tool_schemas()
        self._rebuild_subagent_tools()
        migrate_workspace_plan(self.run_log_path)
        self._reconcile_subagent_registry()

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
            registry_changed = run_log_path != self.run_log_path
            self.run_log_path = run_log_path
            if registry_changed:
                from langbridge_code.agents.common.workspace import configure_agent_artifacts

                configure_agent_artifacts(
                    self.run_log_path, label=self.label, task_name=""
                )
                self._reconcile_subagent_registry()
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
        subagent_tools = {
            "agent_planner": build_agent_planner_tool(
                api_key=self.api_key,
                model=model_for_agent("planner", self.model),
                run_log_path=self.run_log_path,
                turn_id=self.turn_id,
                trace_sink=self.trace_sink,
                phase_sink=self.phase_sink,
                question_callback=self.question_callback,
            ),
            "agent_worker": build_agent_worker_tool(
                api_key=self.api_key,
                model=model_for_agent("worker", self.model),
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
                model=model_for_agent("explorer", self.model),
                run_log_path=self.run_log_path,
                turn_id=self.turn_id,
                trace_sink=self.trace_sink,
                phase_sink=self.phase_sink,
            ),
        }
        self.tools.update(
            {
                name: tool
                for name, tool in subagent_tools.items()
                if self.allowed_tool_names is None or name in self.allowed_tool_names
            }
        )

    def run_goal_loop(
        self,
        goal: SessionGoal,
        *,
        initial_prompt: str | None = None,
        initial_image_paths=None,
        eval_model: str | None = None,
        on_round=None,
        on_verdict=None,
    ) -> tuple[str, SessionGoal]:
        """Run LangBridge rounds until the evaluator confirms the goal or limits hit."""
        evaluator = GoalEvaluatorAgent(
            self.api_key,
            eval_model or model_for_agent("evaluator", self.model),
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

            reply = self.send(prompt, image_paths=initial_image_paths)
            initial_image_paths = None
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

    def run_reviewer_loop(
        self,
        original_request: str,
        *,
        initial_image_paths=None,
        eval_model: str | None = None,
        question_callback=None,
        on_round=None,
        on_verdict=None,
    ) -> str:
        """Run LangBridge rounds until the reviewer releases a reply or the cap is hit.

        Reviewer mode has no predeclared completion spec: the reviewer judges
        each reply against ``original_request`` itself and this never touches
        SessionGoal state.
        """
        evaluator = GoalEvaluatorAgent(
            self.api_key,
            eval_model or model_for_agent("evaluator", self.model),
            run_log_path=self.run_log_path,
            trace_sink=self.trace_sink,
        )
        prompt = original_request
        last_reply = ""

        for _ in range(GOAL_DEFAULT_MAX_TURNS):
            control.checkpoint()
            reply = self.send(prompt, image_paths=initial_image_paths)
            initial_image_paths = None
            last_reply = reply
            if on_round:
                on_round(reply)

            emit_phase(self.phase_sink, "evaluating")
            verdict = evaluator.review(original_request, self.messages, question_callback=question_callback)
            if on_verdict:
                on_verdict(verdict)

            if verdict.release:
                break
            prompt = verdict.next_prompt

        emit_phase(self.phase_sink, "summarizing")
        return last_reply

    def _reconcile_subagent_registry(self):
        """Fix stale registry claims when this session binds to its artifacts.

        Subagents run only as threads of this process, so at bind time nothing
        can still be running: leftover 'working' entries are from a process
        that died and become 'failed' (resumable). Then publish the corrected
        picture to the model via <subagent_state>.
        """
        worktree_mod.reconcile_stale_working(self.run_log_path)
        self._refresh_subagent_state_block(None)

    def _refresh_subagent_state_block(self, background_runner) -> None:
        """Append live runner + registry state at the tail when it changes."""
        pending = background_runner.pending_calls() if background_runner else []
        with self._context_lock:
            stack = self.context.stack
            changed = stack.set_subagent_state_block(
                worktree_mod.build_subagent_state(
                    pending, worktree_mod.registry_snapshot(self.run_log_path)
                )
            )
            # Append-on-change only mutates the stack; rebuild self.messages so
            # the very next model call sees the fresh tail update.
            if changed:
                self.context.sync()

    def _sync_deferred_tool_schemas(self) -> None:
        """Expose only schemas that ToolSearch has already returned."""
        self.tool_schemas = [
            *self._fixed_tool_schemas,
            *(
                []
                if getattr(self, "_native_tool_search", False)
                else self.mcp_registry.loaded_schemas()
            ),
        ]
        self.finalization_tool_schemas = [
            schema
            for schema in self.tool_schemas
            if schema.get("name") not in SUBAGENT_TOOL_NAMES
        ]

    def _refresh_deferred_tool_pool(self, *, force: bool = False) -> None:
        """Append names-only MCP availability changes at the context tail."""
        names = self.mcp_registry.available_tool_names()
        previous = self._announced_mcp_names
        if not force and names == previous:
            return
        if not names and (previous is None or force):
            self._announced_mcp_names = names
            return
        reminder = format_tool_pool_reminder(names, None if force else previous)
        with self._context_lock:
            if self.context.stack.append_system_reminder(reminder):
                self.context.sync()
        self._announced_mcp_names = names

    def _refresh_obsidian_vault_context(self, *, force: bool = False) -> None:
        """Publish the selected local Vault path without adding another tool."""
        vault = obsidian_vault_path()
        current = str(vault) if vault is not None else ""
        previous = self._announced_obsidian_vault
        if not force and current == previous:
            return
        if not current:
            if not previous or force:
                self._announced_obsidian_vault = current
                return
            reminder = (
                "Obsidian Vault configuration changed: no Vault is currently selected. "
                "Do not use the previously configured Vault path."
            )
        elif previous and not force:
            reminder = (
                "Obsidian Vault configuration changed.\n"
                f"Current absolute path: {json.dumps(current, ensure_ascii=False)}\n"
                "Do not use the previously configured Vault path. Obsidian notes are local "
                "Markdown files; use ordinary filesystem tools when the user's request calls "
                "for them."
            )
        else:
            reminder = (
                "Obsidian Vault configured in LangBridge Settings.\n"
                f"Absolute path: {json.dumps(current, ensure_ascii=False)}\n"
                "Obsidian notes are local Markdown files, not an MCP service. Use ordinary "
                "filesystem tools to search, read, create, or edit notes when the user's "
                "request calls for it. Do not ask the user for the Vault path again."
            )
        with self._context_lock:
            if self.context.stack.append_system_reminder(reminder):
                self.context.sync()
        self._announced_obsidian_vault = current

    def _refresh_memory_and_progress_blocks(
        self,
        task="",
        *,
        include_traces=False,
        prefetch_memory_block=True,
        memory_block=None,
    ):
        """Prefetch <memory> and load session_memory.md into head ``<session_memory>``.

        Only on first send (resume) and after context compaction — mid-turn
        ``update_session_memory`` overrides the file without rewriting this block.
        Past user/assistant chat is pinned as ``<history_conversation>``
        (see ``_seed_prior_conversation``); ``include_traces`` remains for tests
        that still want the old resume dump inside ``<session_memory>``.
        """
        from langbridge_code.memory import prefetch_memory

        stack = self.context.stack
        if prefetch_memory_block:
            memory_block = prefetch_memory(
                self.api_key,
                self.model,
                task or self._last_user_prompt or self.target or "",
            )
        stack.set_memory_block(memory_block)
        progress = read_progress(self.run_log_path).strip()
        if progress == SESSION_MEMORY_HEADER.strip():
            progress = ""
        if include_traces:
            from langbridge_code.util.session_traces import build_resume_background

            progress = build_resume_background(
                self.run_log_path, model=self.model, progress=progress
            )
        from langbridge_code.util.progress import clip_session_memory_for_context

        stack.set_session_memory_block(
            clip_session_memory_for_context(progress, run_log_path=self.run_log_path) or None
        )

    def _seed_prior_conversation(self) -> None:
        """Pin prior clean Q&A under ``<history_conversation>``; clear raw rounds.

        Called at the start of every send so the model sees tagged history as
        background, and the new user message as this turn's only live prompt.
        Mid-turn tool rounds still accumulate in raw_rounds after begin_turn.
        """
        from langbridge_code.util.session_traces import (
            format_history_conversation,
            read_conversation,
        )

        stack = self.context.stack
        # Drop prior-turn tool clutter; history is the tagged head block.
        stack.raw_rounds = []
        if self._native_tool_search_history:
            # Client tool-search output is the schema-bearing context for future
            # Responses calls, so it is the one tool round retained across turns.
            stack.raw_rounds.append(copy.deepcopy(self._native_tool_search_history))
        stack.dropped_round_count = 0
        stack._pending_user = None
        stack.subagent_state_block = None
        # The prior reminder was in raw rounds. Re-announce the current pool at
        # the next tail so only clean Q&A remains pinned at the head.
        self._announced_mcp_names = None
        self._announced_obsidian_vault = None
        conversation = read_conversation(
            self.run_log_path,
            exclude_pending_turn_id=self.turn_id,
        )
        stack.set_history_conversation_block(
            format_history_conversation(conversation) if conversation else None
        )
        self.context.sync()

    def _run_native_tool_search(self, call: dict) -> tuple[dict, str | None]:
        """Execute one OpenAI client tool-search request and return wire output."""
        raw_arguments = call.get("arguments") or {}
        try:
            arguments = (
                json.loads(raw_arguments)
                if isinstance(raw_arguments, str)
                else dict(raw_arguments)
            )
            selection = self.mcp_registry.search(arguments.get("query", ""))
            tools = self.mcp_registry.native_search_tools(selection)
            error = None
        except Exception as exception:
            tools = []
            error = f"Tool search error: {exception}"
        result = {
            "type": "tool_search_output",
            "execution": "client",
            "call_id": call.get("call_id"),
            "status": "completed",
            "tools": tools,
        }
        call_id = call.get("call_id")
        if call_id and not any(
            item.get("call_id") == call_id
            for item in self._native_tool_search_history
        ):
            self._native_tool_search_history.extend(
                [copy.deepcopy(call), copy.deepcopy(result)]
            )
        self._sync_deferred_tool_schemas()
        return result, error

    def _restore_native_tool_search_history(self) -> None:
        """Keep schema-bearing search pairs alive when context compaction drops them."""
        if not self._native_tool_search_history:
            return
        present = {
            (item.get("type"), item.get("call_id"))
            for round_items in self.context.stack.raw_rounds
            for item in round_items
            if item.get("type") in {"tool_search_call", "tool_search_output"}
        }
        missing: list[dict] = []
        for item in self._native_tool_search_history:
            identity = (item.get("type"), item.get("call_id"))
            if identity not in present:
                missing.append(copy.deepcopy(item))
        if missing:
            self.context.stack.raw_rounds.insert(0, missing)
            self.context.sync()

    def _init_context_blocks(self, user_prompt):
        """First-send pin: <memory> + <session_memory> + full <skill_index> listing."""
        from langbridge_code.skills import (
            attach_skill_tracking,
            ensure_skill_index_block,
            langbridge_skill_catalog,
        )

        # A session fork carries the source's selected memory as a one-shot
        # snapshot. Reuse it verbatim so the branch keeps the same prompt prefix
        # instead of selecting memory again from its first divergent question.
        has_fork_memory, fork_memory = read_fork_memory_context(self.run_log_path)
        self._refresh_memory_and_progress_blocks(
            task=user_prompt,
            include_traces=False,
            prefetch_memory_block=not has_fork_memory,
            memory_block=fork_memory,
        )
        ensure_skill_index_block(
            self.context.stack,
            self.api_key,
            self.model,
            user_prompt,
            langbridge_skill_catalog(),
            label="LangBridge skill listing",
        )
        attach_skill_tracking(self.context.stack, self.tools, role="langbridge")
        previous = self.context.stack.on_compacted

        def on_compacted(_stack):
            if previous is not None:
                try:
                    previous(_stack)
                except Exception:
                    pass
            self._refresh_memory_and_progress_blocks()
            # A compacted reminder is not a head pin. Re-publish the names so
            # the model can load a schema again if needed.
            self._refresh_deferred_tool_pool(force=True)
            self._refresh_obsidian_vault_context(force=True)

        self.context.stack.on_compacted = on_compacted
        self._context_blocks_ready = True
        if has_fork_memory:
            clear_fork_memory_context(self.run_log_path)

    @staticmethod
    def _background_result_event(completed_calls) -> str:
        sections = []
        for completed in completed_calls:
            call = completed.call
            try:
                arguments = without_description(
                    json.loads(call.get("arguments") or "{}"), call.get("name") or ""
                )
            except (TypeError, ValueError):
                arguments = {}
            identity = ", ".join(
                value
                for value in (
                    f"description={arguments.get('description')!r}" if arguments.get("description") else "",
                    f"task_name={arguments.get('task_name')!r}" if arguments.get("task_name") else "",
                )
                if value
            )
            heading = f"{call.get('name')} call_id={call.get('call_id')}"
            if identity:
                heading += f" ({identity})"
            sections.append(f"{heading}\n{completed.output.get('output', '')}")
        body = "\n\n---\n\n".join(sections)
        return (
            "<background_tool_results>\n"
            "Previously launched subagent calls have completed. Process every "
            "result now: note it, merge and check off PASS tasks, handle failures "
            "(inspect the reason — resume same task_name, or reset: new id + "
            "discard old worktree, then dispatch), and dispatch newly unblocked "
            "work while other background calls continue.\n\n"
            f"{body}\n"
            "</background_tool_results>"
        )

    def _deliver_background_results(self, completed_calls) -> None:
        if not completed_calls:
            return
        with self._context_lock:
            self.context.begin_turn(self._background_result_event(completed_calls))
        for completed in completed_calls:
            write_worklog_observation(
                self.run_log_path,
                self.label,
                self.worklog_id,
                self.turn_id,
                self.step,
                completed.output,
            )

    def _run_completion_driven_tool_step(self, tool_calls, background_runner):
        background_calls = [
            call for call in tool_calls if call.get("name") in BACKGROUND_TOOL_NAMES
        ]
        foreground_calls = [
            call for call in tool_calls if call.get("name") not in BACKGROUND_TOOL_NAMES
        ]
        outputs_by_id = {}
        deferred = []

        if background_calls:
            background_runner.submit(background_calls)

        if foreground_calls:
            for output in run_tool_calls(self._run_tool, foreground_calls):
                outputs_by_id[output.get("call_id")] = output

        if background_calls:
            completed = background_runner.drain_completed(
                wait_for_one=not foreground_calls
            )
            current_ids = {call.get("call_id") for call in background_calls}
            for item in completed:
                call_id = item.call.get("call_id")
                if call_id in current_ids:
                    outputs_by_id[call_id] = item.output
                else:
                    deferred.append(item)

        outputs = []
        for call in tool_calls:
            call_id = call.get("call_id")
            output = outputs_by_id.get(call_id)
            if output is None:
                output = {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": BACKGROUND_PENDING,
                }
            outputs.append(output)
        return outputs, deferred

    def send(self, user_prompt, *, image_paths=None):
        from langbridge_code.skills import (
            expand_skill_slash,
            list_skills,
            parse_skill_slash,
            record_invoked_skill,
            resolve_skill_slash,
        )

        # Remembered so the post-compaction <memory> re-prefetch targets the
        # current task instead of an empty string. Keep the raw slash text so
        # memory prefetch still sees "/grilling …" rather than the expanded body.
        raw_prompt = (user_prompt or "").strip()
        try:
            user_prompt = expand_skill_slash(raw_prompt)
        except FileNotFoundError as error:
            available = ", ".join(name for name, _ in list_skills(role="langbridge"))
            return (
                f"Unknown skill '/{error}'. "
                f"Available skills: {available or '(none)'}. Try /help for built-in commands."
            )
        self._last_user_prompt = raw_prompt
        self._memory_writer_ran_this_send = False
        self._deadline_finalizing = False
        if not self._context_blocks_ready:
            self._init_context_blocks(raw_prompt)
        with self._context_lock:
            self._seed_prior_conversation()
        self._refresh_deferred_tool_pool()
        self._refresh_obsidian_vault_context()
        if resolve_skill_slash(raw_prompt)[0] == "expanded":
            parsed = parse_skill_slash(raw_prompt)
            if parsed:
                record_invoked_skill(self.context.stack, parsed[0], role="langbridge")
        from langbridge_code.llm.images import user_content_with_images

        turn_content = user_content_with_images(
            build_turn_user_content(self.run_log_path, user_prompt), image_paths
        )
        with self._context_lock:
            self.context.begin_turn(turn_content)
        write_worklog_received(self.run_log_path, self.label, self.worklog_id, self.turn_id, raw_prompt)
        foreground = ForegroundTracker(self.label, self.messages, self.model)
        foreground.activate()
        start_time = now()
        self._turn_start_time = start_time
        background_runner = CompletionDrivenToolRunner(self._run_tool)
        deferred_background_results = []
        try:
            for _ in range(MAX_AGENT_STEPS):
                control.checkpoint()
                if over_time_budget(start_time, MAX_AGENT_SECONDS):
                    self._deadline_finalizing = True
                    return self._finish(f"{self.label} stopped: out of time.")
                self._deadline_finalizing = in_finalization_window(start_time)
                completed = [
                    *deferred_background_results,
                    *background_runner.drain_completed(),
                ]
                deferred_background_results = []
                self._deliver_background_results(completed)
                self._refresh_subagent_state_block(background_runner)
                self._refresh_deferred_tool_pool()
                self._refresh_obsidian_vault_context()
                with self._context_lock:
                    self.context.compact_to_budget(model=self.model)
                    self._restore_native_tool_search_history()
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
                        request_messages(
                            self.messages,
                            self.model,
                            finalizing=self._deadline_finalizing,
                        ),
                        tool_schemas=(
                            self.finalization_tool_schemas
                            if self._deadline_finalizing
                            else self.tool_schemas
                        ),
                        reasoning={"summary": "auto"},
                        label=self.label,
                        stream_sink=self.trace_sink,
                    )
                )
                output = response.get("output", [])
                tool_calls = [item for item in output if item.get("type") == "function_call"]
                search_calls = [
                    item for item in output if item.get("type") == "tool_search_call"
                ]
                if not tool_calls and not search_calls:
                    print_step_trace(output, include_message=True, label=self.label, sink=self.trace_sink)
                    if output:
                        with self._context_lock:
                            finish_step(self.context, list(output), self, budget)
                        foreground.publish()
                    if background_runner.has_pending():
                        completed = background_runner.drain_completed(wait_for_one=True)
                        self._deliver_background_results(completed)
                        continue
                    return self._finish(extract_output_text(output))
                print_step_trace(output, include_message=True, label=self.label, sink=self.trace_sink)
                step_items = list(output)
                for search_call in search_calls:
                    search_output, search_error = self._run_native_tool_search(search_call)
                    step_items.append(search_output)
                    if search_error:
                        step_items.append({
                            "role": "user",
                            "content": f"<system-reminder>{search_error}</system-reminder>",
                        })
                    write_worklog_observation(
                        self.run_log_path,
                        self.label,
                        self.worklog_id,
                        self.turn_id,
                        self.step,
                        search_output,
                    )
                tool_outputs, deferred = self._run_completion_driven_tool_step(
                    tool_calls, background_runner
                ) if tool_calls else ([], [])
                deferred_background_results.extend(deferred)
                browser_images = []
                for tool_output in tool_outputs:
                    browser_images.extend(tool_output.pop("_image_paths", []))
                    step_items.append(tool_output)
                    write_worklog_observation(
                        self.run_log_path, self.label, self.worklog_id, self.turn_id, self.step, tool_output
                    )
                if browser_images:
                    from langbridge_code.llm.images import user_content_with_images

                    step_items.append(
                        {
                            "role": "user",
                            "content": user_content_with_images(
                                "Inspect this screenshot from the current X browser page.",
                                browser_images,
                            ),
                        }
                    )
                self.step += 1
                with self._context_lock:
                    finish_step(self.context, step_items, self, budget)
                if not self._deadline_finalizing:
                    self._maybe_force_session_memory()
                foreground.publish()
            return self._finish(f"{self.label} stopped: max steps.")
        finally:
            background_runner.close()
            # The runner is gone; republish so no stale RUNNING line survives
            # into the next turn's context.
            self._refresh_subagent_state_block(None)
            foreground.deactivate()
            self._turn_start_time = None

    def _maybe_force_session_memory(self):
        """After too many silent rounds, fork-write session_memory.md (code-enforced)."""
        self._rounds_since_session_memory += 1
        if self._rounds_since_session_memory <= SESSION_MEMORY_REMINDER_ROUNDS:
            return
        # Reset only on success; a failed fork retries next round so compaction
        # never drops rounds that no note covers.
        if self._note_write_succeeded(self._write_session_memory_via_fork()):
            self._rounds_since_session_memory = 0

    @staticmethod
    def _note_write_succeeded(result) -> bool:
        return str(result or "").startswith("Noted")

    def _write_session_memory_via_fork(self):
        """Fork an Edit-restricted note-writer; update session_memory.md in place.

        Does not inject into ``<session_memory>`` — that block is loaded only on
        resume / compaction. Raw messages already carry the full transcript.
        """
        from langbridge_code.agents.common.fork import fork_session_memory

        try:
            return fork_session_memory(
                self.api_key,
                self.model,
                self.messages,
                run_log_path=self.run_log_path,
                turn_id=self.turn_id,
                tool_schemas=MAIN_AGENT_TOOL_SCHEMAS,
                label="session memory fork",
            )
        except Exception as error:
            return f"Session memory fork failed: {error}"

    def _run_tool(self, call):
        name = call.get("name")
        call_id = call.get("call_id")
        image_paths = []
        try:
            if (
                name in SUBAGENT_TOOL_NAMES
                and self._turn_start_time is not None
                and in_finalization_window(self._turn_start_time)
            ):
                self._deadline_finalizing = True
                raise RuntimeError(
                    f"{name} is disabled during the deadline finalization window"
                )
            arguments = without_description(json.loads(call.get("arguments") or "{}"), name)
            if name == "ask_user":
                output = resolve_ask_user(arguments, self.question_callback)
            elif name == "ToolSearch":
                selection = self.mcp_registry.search(arguments.get("query", ""))
                self._sync_deferred_tool_schemas()
                output = json.dumps(selection, ensure_ascii=False)
            elif name == "update_session_memory":
                output = self._write_session_memory_via_fork()
                if self._note_write_succeeded(output):
                    self._rounds_since_session_memory = 0
            elif name == "memory_writer":
                from langbridge_code.tools.memory_writer import schedule_memory_writer

                output = schedule_memory_writer(
                    self.api_key,
                    self.model,
                    list(self.messages),
                )
                self._memory_writer_ran_this_send = True
            elif (
                getattr(self, "mcp_registry", None) is not None
                and self.mcp_registry.is_loaded_call(name)
            ):
                self._require_tool_approval(name, arguments)
                output = self.mcp_registry.call(name, arguments)
            elif name not in self.tools:
                raise ValueError(f"Unknown {self.label} tool: {name}")
            else:
                self._require_tool_approval(name, arguments)
                if name == "merge_branch":
                    arguments["run_log_path"] = self.run_log_path
                from langbridge_code.tools.execution import attach_run_log_path

                attach_run_log_path(name, arguments, self.run_log_path)
                plan_target = (
                    artifact_plan_path(self.run_log_path)
                    if name in PLAN_FILE_TOOL_NAMES
                    else None
                )
                with plan_file_scope(plan_target):
                    output = self.tools[name](**arguments)
                if name == "browser":
                    try:
                        payload = json.loads(output)
                        image_paths = list(payload.get("image_paths") or [])
                    except (TypeError, json.JSONDecodeError):
                        pass
        except Exception as error:
            output = f"Tool error: {error}"
        result = {"type": "function_call_output", "call_id": call_id, "output": output}
        if image_paths:
            result["_image_paths"] = image_paths
        return result

    def _require_tool_approval(self, name, arguments) -> None:
        callback = self.approval_callback
        if callback is None or not approval_callback_required(callback, name, arguments):
            return
        if not callback(self.label, name, arguments):
            reason = approval_reason(name, arguments) or "blocked by the active permission mode"
            raise PermissionError(f"{name} was not approved ({reason})")

    def _finish(self, report):
        from langbridge_code.tools.memory_writer import schedule_memory_writer

        write_worklog_finish(self.run_log_path, self.label, self.worklog_id, self.turn_id, report)
        # Catch progress omitted since the last note (same idea as Memory Writer).
        if self._rounds_since_session_memory > 0 and not self._deadline_finalizing:
            if self._note_write_succeeded(self._write_session_memory_via_fork()):
                self._rounds_since_session_memory = 0
        # A mid-turn Memory Writer already reconciled this context. Otherwise
        # fork the same tool-using writer in the background to catch omissions.
        if not self._memory_writer_ran_this_send and not self._deadline_finalizing:
            schedule_memory_writer(self.api_key, self.model, self.messages)
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
    allowed_tool_names=None,
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
            allowed_tool_names=allowed_tool_names,
        )
        reply = session.send(target)
        emit_phase(phase_sink, "summarizing")
        if print_reply:
            print(f"\n{reply}\n")
        outcome = reply or ""
    except control.StopRequested:
        outcome = "Stopped by user."
    except Exception as error:
        from langbridge_code.llm.client import format_api_error

        outcome = format_api_error(error)
    finally:
        end_trace()
    return outcome
