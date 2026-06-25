> Note to self: update the README and amend the commit based on this design.

# LangBridge: Loop-Engineering Architecture

When a user gives a task, we call it the **`user_task`**.

The system is built from **nested loops**, with four roles:

- **PM** — the outer loop; acts like a whip that keeps driving the work.
- **L4** — implements a normal `component_task`.
- **L5** — implements a hard `component_task` by divide-and-conquer.
- **L3** — the tester, shared inside both the L4 and L5 loops.

### Loop limits (every loop)

Every loop below has three independent limits:

1. **Context length** — when the loop makes LLM calls (compact or summarize first; fail only if it still overflows).
2. **Timeout** — a wall-clock budget.
3. **Max loop count** — a hard cap on the number of turns.

Whichever limit trips first stops the loop and marks it a `failure`. A `failure` escalates to the level above (L4 → PM, L5 → PM). When the PM exhausts its own limits, it reports a clear blocker to the user.

---

## 1. PM — the outer loop

### Structure

- The PM loop is the outermost loop. The PM acts like a whip: it keeps driving the work forward.

### Role

- The PM breaks the `user_task` into a **`todo_list`** state file at the **`component_task`** level. A `component_task` is not technical, or at least not deeply technical.
- For each `component_task`, the PM calls the **L4** or **L5** agentic tool to execute it.
- The PM decides whether a `component_task` goes to L4 or L5. It knows the L4/L5 difference from its role prompt (described below).
- When L4 or L5 returns the `component_task` delivery, the PM verifies it:
  - **Good** → accept it, mark that `component_task` as done in the `todo_list`, and move to the next one. If it was the last `component_task`, mark the `user_task` complete and return to the user.
  - **Not good** → write the PM's opinion into that `component_task` in the `todo_list` (do **not** mark it done), and send it back to L4 or L5 to rework.
- The **last `component_task` is always an e2e test** for the whole `user_task`. It runs through the normal L4/L5 + L3 + jury machinery like any other `component_task`.
- After all `component_task`s pass, if the deliverable is runnable, the PM brings up the project and plays around with it to debug by hand. If it finds nothing, it marks the `user_task` complete and returns to the user. If it finds a bug, it opens a **new `component_task`** to fix it (bounded by the PM's loop limits).

---

## 2. L4 agentic tool

### Structure

- Each L4 tool call runs one **L4 agentic tool loop**. L4 and L3 share this same loop.
- The loop keeps a **`shared_worklog`**: the conversation between L4 and L3.
- L4 and L3 each keep a private log, **`l4_worklog`** and **`l3_worklog`**, visible only to themselves.
  - When L3 works, it reads `l3_worklog` + `shared_worklog`.
  - When L4 works, it reads `l4_worklog` + `shared_worklog`.

### Role (how the loop runs)

L4 receives a `component_task` from the PM and can read the `todo_list` for background. Each turn, exactly one of L4 or L3 is active, decided by the last token in `shared_worklog`: empty or `concern exist` → **L4's turn**; `ready` or `push back` → **L3's turn**.

- **L4 not ready (L4 implements):** L4 thinks and acts each turn (L3 is skipped). When L4 thinks the `component_task` is done, it appends its delivery + `ready` to `shared_worklog`, then goes to the next turn.
- **L4 ready (L3 tests):** L3 becomes active. It writes tests and runs the needed commands against L4's implementation.
  - **L3 says good** → the L4 loop ends and returns the delivery to the PM to accept or reject.
  - **L3 says not good** → L3 appends its idea + `concern exist` to `shared_worklog`; next turn L4 is active and works on the concern.
    - **L4 agrees with the concern** → works until it is `ready` again.
    - **L4 disagrees** → pushes back by appending its opinion + `push back` to `shared_worklog`. Next turn L3 is active because of the push back.
      - **L3 accepts the push back** → adjusts how it tests.
        - Tests pass → `pass`: end the L4 loop, deliver to the PM.
        - Tests still fail → append idea + `concern exist`, go to the next turn.
      - **L3 thinks the push back is unreasonable** → convene a **jury of 2 fresh, independent testers**. Each writes its own tests and votes. The original L3's opinion is set aside; only the 2 jurors decide.
        - **Both vote yes** → `pass`: end the L4 loop, deliver to the PM.
        - **Otherwise (one or both vote no)** → `failure`: return to the PM to retry or reassign to L5.

---

## 3. L5 agentic tool (Ralph loop)

### Structure

- Each L5 tool call runs one **L5 Ralph loop**.
- Every Ralph turn triggers one **L5 agentic tool loop**, with the same prompt, for a single **`technical_sub_task`**.
- L5 and L3 share this L5 agentic tool loop.
- The loop keeps a **`shared_worklog`**: the conversation between L5 and L3.
- L5 and L3 each keep a private log, **`l5_worklog`** and **`l3_worklog`**, visible only to themselves.
  - When L3 works, it reads `l3_worklog` + `shared_worklog`.
  - When L5 works, it reads `l5_worklog` + `shared_worklog`.

### Ralph loop

- The Ralph loop receives a **HARD** `component_task` from the PM.
- It keeps prompting the L5 agentic tool loop with the same prompt until every `technical_sub_task` in the **`component_task_plan`** is done.
- Then it returns the delivery to the PM to accept or reject.

### Role (how the loop runs)

L5 receives the HARD `component_task` from the Ralph loop and can read the `todo_list` for background.

First, the L5 agentic tool loop searches for and reads the **`component_task_plan`** to learn where to start. This file is uniquely identified. It holds the plan that splits the `component_task` into several `technical_sub_task` items to conquer one by one, and the **last `technical_sub_task` must be an integration test** for the `component_task`. If the plan does not exist, L5 creates it and writes the plan.

Each turn, exactly one of L5 or L3 is active, decided by the last token in `shared_worklog`: empty or `concern exist` → **L5's turn**; `ready` or `push back` → **L3's turn**.

- **L5 not ready (L5 implements):** L5 thinks and acts on the current `technical_sub_task` (L3 is skipped). When L5 thinks it is done, it appends its delivery + `ready` to `shared_worklog`, then goes to the next turn.
- **L5 ready (L3 tests):** L3 becomes active. It writes tests and runs the needed commands against L5's implementation.
  - **L3 says good** → mark that `technical_sub_task` complete, end the L5 agentic tool loop, and return to the Ralph loop.
    - If the Ralph loop sees all sub-tasks done → return the delivery to the PM to accept or reject.
    - If sub-tasks remain → continue the Ralph loop and send the same prompt to the next L5 agentic tool loop.
  - **L3 says not good** → L3 appends its idea + `concern exist`; next turn L5 works on the concern.
    - **L5 agrees with the concern** → works until it is `ready` again.
    - **L5 disagrees** → pushes back by appending its opinion + `push back` to `shared_worklog`. Next turn L3 is active because of the push back.
      - **L3 accepts the push back** → adjusts how it tests.
        - Tests pass → `pass`: mark the `technical_sub_task` complete, end the L5 loop, return to the Ralph loop.
        - Tests still fail → append idea + `concern exist`, go to the next turn.
      - **L3 thinks the push back is unreasonable** → convene a **jury of 2 fresh, independent testers** (original L3 set aside; only the 2 jurors decide).
        - **Both vote yes** → `pass`: mark the `technical_sub_task` complete, end the L5 loop, return to the Ralph loop.
        - **Otherwise** → `failure`: **escalate to the PM** (with the worklog and the failing `technical_sub_task`) to re-scope or re-plan the `component_task`.

---

## General questions

- If the user asks a general question (not a task), the PM should answer it directly.
