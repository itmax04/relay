---
name: relay-handoff
description: Write or restore a compact handoff so work continues in a fresh session instead of a long, expensive chat. Use when a relay message says the context is over its limit, when the user asks to wrap up, hand off, or start a new session, at the end of a finished step, or at the start of a session that continues earlier work.
---

# relay-handoff

A long chat re-sends its whole history with every call, so the cheapest way to keep working
is a fresh session that starts from a short, verified state file. This skill writes that file
and restores work from it. It works the same for any agent and any model.

## Where the handoff lives

1. If the project's own instructions (AGENTS.md, CLAUDE.md, an SDLC process) define a handoff
   or state document for the current change, update that one. Never create a second source of
   state next to it.
2. Otherwise use the path from `.relay/config.json` (`handoff_path`), default `.relay/handoff.md`.

Write the handoff in the language the project keeps its documents in.

## Writing a handoff

Update the file in place; keep it short enough to read in one pass (usually under 150 lines).
Link to evidence instead of pasting it. Use these sections:

- **Identity**: task or change id, branch, commit SHA (`git rev-parse --short HEAD`), and
  uncommitted changes listed separately (`git status --short`).
- **Goal and scope**: what is being built, acceptance criteria, owner decisions and constraints,
  quoted exactly where wording matters.
- **Done**: what changed, with file paths.
- **Verified**: exact commands run and their results, and the commit they ran against.
  Mark anything not run as NOT RUN. Never report a check as passing from memory.
- **Open**: known defects, blockers, risks, rejected approaches with one line on why.
- **Next**: the single next action, the files it needs, the checks and reviews required
  before it counts as done, and when to stop and ask the owner.

Keep facts, assumptions and unknowns apart. Keep exact error messages and reproduction steps
for an unfixed bug. Do not drop a constraint or a failed check to make the file shorter.

After writing it, tell the user in one or two lines where the handoff is and that the next
step should start in a new session with: "Continue from the handoff at <path>."

## Restoring from a handoff

1. Read the project's mandatory instructions, then the handoff.
2. Check the branch, commit SHA and uncommitted changes against the handoff. If they differ,
   say so and trust the repository and actual check results over the handoff.
3. Read only the files the next action needs. Do not re-explore the project or reread old chat
   history unless the handoff leaves a specific gap.
4. Do only the next agreed action, with its checks and reviews, then update the handoff again.

## When not to hand off

In the middle of an unresolved debugging loop a fresh session can cost more than finishing the
loop: hand off once the cause is found or the attempt is abandoned, unless the context is over
the hard limit.
