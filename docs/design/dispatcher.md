# relay — dispatcher (phase 3) design

Status: agreed with the owner on 2026-11-03. Nothing is implemented yet.

The dispatcher (`relay run`) works through a plan one step at a time. Each step runs in a fresh
agent process, with the model and effort chosen from the step's tier and the current quota. Short
contexts per step are the point: the same reason the guard exists.

## Flow

1. **Plan.** `.relay/plan.md` in the target project. A strong model drafts it from a short task and
   the owner approves it before the run starts; a hand-written plan is accepted too. Each step has:
   - title and tier `[strong|standard|light]`;
   - goal and "done when";
   - check command(s);
   - optional: allowed files, `[confirm]`, pinned agent (`claude` / `codex`).
2. **Tier → model.** A config table maps each tier to model + effort, separately for Claude and
   Codex. Model names come from `relay/catalog.py` (Codex: `~/.codex/models_cache.json`).
3. **One process per step.**
   - Claude: `claude -p --output-format stream-json --model … --effort … --permission-mode acceptEdits --allowedTools …`
   - Codex: `codex exec --json -m … -c model_reasoning_effort=… -s workspace-write -C <worktree>`

   The prompt is the step text plus the previous step's summary, never the long history.
4. **Checks.** The step's local commands, a non-empty diff, and no edits outside the allowed files.
   No LLM judge in v1.
5. **Failure.** Retry with the error output, at most 2 times, then escalate the tier
   (light → standard → strong), then stop the run and notify the owner. The agent is never switched
   silently.
6. **Handoff between steps.** The agent ends with a "Step summary" block. The dispatcher stores it
   with the diff stat and passes it to the next step.

## Agent choice and quota

- Preference order in config.
- Move away from an agent when its 5-hour window is above 80% or its weekly projection runs out.
- A step may pin an agent.
- Both agents exhausted: wait for the earliest window reset.
- Sources: Codex `rate_limits` from `codex exec --json` / rollouts; Claude from the status line
  (`quota.jsonl`) and, if present, rate-limit events in `claude -p` stream-json.

v1 runs on Codex only. Claude is added after the owner runs `claude auth login`. On 2026-11-03 the
Claude CLI was not logged in, while Codex was logged in through ChatGPT.

## Autonomy limits

- Work happens only in a git worktree on branch `relay/run-<id>`, with one commit per step.
- The dispatcher never pushes. The owner merges.
- Claude runs with `acceptEdits` and an allowlist of tools. Codex runs in the `workspace-write` sandbox.
- Each run is capped at N steps and T minutes.
- A `[confirm]` step pauses the run until the owner approves it.

## State and app

- Run state lives in `~/.local/state/relay/runs/<id>/`: plan, per-step logs, summaries and check output.
- `relay watch` serves it as `GET /api/runs` with SSE updates.
- App v1:
  - a Runs screen;
  - a popover line such as "step 3/7 · Sonnet 5.5 · attempt 1";
  - Stop and Approve-plan buttons.

  Starting a run from the app comes later.

## Control

Plain CLI subprocesses: no Agent SDK, no Codex app-server, no new dependencies. A step never
changes the model midway. A new tier means a new process.

## To verify while building

- Whether `claude -p` stream-json carries rate-limit information. The Agent SDK documents
  `rate_limit_event`.
- Where Codex quota appears in `codex exec --json` output: `turn.completed` or the rollout's `rate_limits`.
- Exact Codex model names for each tier.
