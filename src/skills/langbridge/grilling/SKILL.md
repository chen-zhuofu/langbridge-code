---
name: grilling
description: Grill the user relentlessly about a plan, decision, or idea. Use when the user wants to stress-test a plan/design before coding, or says grill / grill me / grilling.
---

## LangBridge Code mapping (main agent)

Run the session yourself, before you plan or implement. Ask via `ask_user` only —
one question per call. Prefer looking up facts with tools (read files, explore)
over asking. Do not start agent_planner / agent_worker until the user confirms
shared understanding.

# grilling

Interview me relentlessly about every aspect of this until we reach a shared understanding. Walk down each branch of the decision tree, resolving dependencies between decisions one-by-one. For each question, provide your recommended answer.

Ask the questions one at a time, waiting for feedback on each question before continuing. Asking multiple questions at once is bewildering.

If a *fact* can be found by exploring the environment (filesystem, tools, etc.), look it up rather than asking me. The *decisions*, though, are mine — put each one to me and wait for my answer.

Do not act on it until I confirm we have reached a shared understanding.
