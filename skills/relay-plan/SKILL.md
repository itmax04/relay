---
name: relay-plan
description: Plan a multi-stage task as stages with a model tier each, and stop at stage boundaries with a one-line model recommendation for the next session. Use at the start of a task with several stages (not for questions or small edits), at a stage boundary, and when the context grows past the limit in the relay block.
---

# relay-plan

A strong model is wasted on mechanical work, and a long chat re-sends its whole history with
every call. So a multi-stage task is split into stages, each stage gets the cheapest tier that
can do it, and work continues in a fresh session when the tier changes. The agent only advises:
the user switches the model, never the agent.

## Tiers

- strong: design, hard debugging.
- standard: implementation from a plan.
- light: mechanical edits, tests, docs.

The model and effort for each tier, and the context limit, are in the "Tiers" line of the relay
block in your global instructions (`~/.claude/CLAUDE.md` or `~/.codex/AGENTS.md`). Use exactly
those names; relay keeps them current. In Claude Code the user switches with `/model`.

## When to use it

- Use it for a task with several stages. For a question or a small edit, just answer.
- At the start: a short plan, one line per stage, each with its tier.
- Stop only at a stage boundary, and only when the next stage has a different tier or the
  context is above the limit in the relay block. Otherwise go on to the next stage in the same session.
- Never ask to switch the model in the middle of a session: the next stage starts in a new
  session from the handoff.
- Every time a session ends and the work goes on in a new one (a stage boundary, the context
  limit, or a finished step that has a next step), end with the recommendation line below, even
  when the next session keeps the same tier.

## Before stopping

1. Verify the stage: run its checks and look at the result.
2. Update the handoff with the relay-handoff skill (its Next section names the next stage).
3. End with the recommendation line:

   `<next stage>, <tier> → <model> <effort>, in a new session. Prompt: «<prompt>»`

   - Model and effort: exactly the names from the Tiers line for that tier.
   - The prompt names the concrete next action (what to do, on which PR, task or files, from
     which handoff), so the new session can start without asking. Not just "continue".
4. Right after it, write the machine tag as the very last line, exactly (one-line JSON, the same
   prompt, `model` and `effort` as in the line above; relay reads it to offer the new session):

   `<!-- relay-next {"model": "<model>", "effort": "<effort>", "prompt": "<prompt>"} -->`

## Examples

Reply in the user's language; these examples are in English.

Plan at the start:

```
Plan:
1. Design the block format and installation locations — strong
2. Implement install/uninstall and tests — standard
3. Update README and status — light
Starting stage 1.
```

Checkpoint at a stage boundary (the next stage has another tier):

```
Stage 1 is done: the block format is agreed, tests pass (12 OK). Handoff updated: .relay/handoff.md.
➡️ Next stage — implement install/uninstall, standard → Sonnet 5.5 medium, in a new session. Prompt: «Continue from .relay/handoff.md: implement block install/uninstall and their tests».
<!-- relay-next {"model": "Sonnet 5.5", "effort": "medium", "prompt": "Continue from .relay/handoff.md: implement block install/uninstall and their tests"} -->
```

End of a session in Codex (a review stage is done, delivery is next):

```
Review complete: APPROVE posted on PR #83. Handoff updated.
➡️ Next stage — delivery, standard → gpt-6.1-sol medium, in a new session. Prompt: «Deliver PR #83 from the handoff, taking the posted APPROVE into account, then prepare the handoff for T-6a2b».
<!-- relay-next {"model": "gpt-6.1-sol", "effort": "medium", "prompt": "Deliver PR #83 from the handoff, taking the posted APPROVE into account, then prepare the handoff for T-6a2b"} -->
```

Stop because of the context size (same tier, but the context is above the 150K limit):

```
⏸ Context is 162K; continuing is expensive. Stage 2 is halfway done: install is implemented and verified, uninstall is not.
Handoff updated: .relay/handoff.md.
➡️ Next stage — stage 2 (continued): uninstall and tests, standard → Sonnet 5.5 medium, in a new session. Prompt: «Continue from .relay/handoff.md: finish uninstall and the install/uninstall tests».
<!-- relay-next {"model": "Sonnet 5.5", "effort": "medium", "prompt": "Continue from .relay/handoff.md: finish uninstall and the install/uninstall tests"} -->
```

A small question gets a plain answer, without a plan:

```
> What port does the engine use?
127.0.0.1:8765.
```
