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

## Before stopping

1. Verify the stage: run its checks and look at the result.
2. Update the handoff with the relay-handoff skill (its Next section names the next stage).
3. End with one line: the next stage, its tier and model, and the prompt for the new session.

## Examples

Reply in the user's language; these examples are in Russian.

Plan at the start:

```
План:
1. Спроектировать формат блока и места установки — strong
2. Реализовать install/uninstall и тесты — standard
3. Обновить README и status — light
Начинаю этап 1.
```

Checkpoint at a stage boundary (the next stage has another tier):

```
Этап 1 готов: формат блока согласован, тесты проходят (12 OK). Handoff обновлён: .relay/handoff.md.
➡️ Следующий: этап 2 — реализовать install/uninstall. Уровень: standard → Sonnet 5.5, effort medium.
Начни новую сессию с промптом: «Continue from the handoff at .relay/handoff.md.»
```

Stop because of the context size (same tier, but the context is above the 150K limit):

```
⏸ Контекст 162K — дальше дорого. Этап 2 сделан наполовину: install готов и проверен, uninstall нет.
Handoff обновлён: .relay/handoff.md.
➡️ Следующий: этап 2 (продолжение) — uninstall и тесты. Уровень: standard → Sonnet 5.5, effort medium.
Начни новую сессию с промптом: «Continue from the handoff at .relay/handoff.md.»
```

A small question gets a plain answer, without a plan:

```
> Какой порт у движка?
127.0.0.1:8765.
```
