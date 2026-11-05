---
description: Run one build phase from prompts/ (e.g. /phase 03)
argument-hint: <phase number>
---
Run build phase $ARGUMENTS.

1. Read `CONTEXT-HANDOFF.md`, `CLAUDE.md`, `docs/DECISIONS.md`, `docs/PROGRESS.md`, and the prompt in `prompts/` starting with `$ARGUMENTS`, plus every doc section it lists.
2. The previous phase's gate (and any ⛔ human gate before it) must be marked passed in PROGRESS.md. If not, stop and say so.
3. Plan in 3–5 lines (files, tests, which builder subagents take what).
4. Build with the subagents in `.claude/agents/`, in parallel where folders don't overlap.
5. Run the gate checks yourself and look at the real output.
6. Run the `reviewer` subagent. Fix every FAIL.
7. Update `docs/PROGRESS.md` (and DECISIONS for any choice), commit, send a 5-line report: gate PASS/FAIL, what you saw, what's next, blockers, HUMAN_TODO items.
8. If the phase has a ⛔ human gate, stop and wait for sign-off.
