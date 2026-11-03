# relay — dispatcher (phase 3) design

Status: agreed with the owner on 2026-11-03. v1 (Codex only) implemented on 2026-11-03:
`relay/plan.py`, `relay/dispatch.py`, `relay plan`, `relay run`, `/api/runs`, the app's Runs screen.

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

## Found while building (2026-11-03)

- `codex exec --json` (0.144.6) emits `thread.started`, `item.*`, `turn.completed {usage}` or
  `turn.failed {error.message}`; no rate limits. The rollout
  `~/.codex/sessions/YYYY/MM/DD/rollout-*-<thread_id>.jsonl` carries `rate_limits` (primary 5 h,
  secondary week), which the dispatcher reads after each step.
- Codex CLI 0.144.6 with a ChatGPT login accepts only `gpt-5.6-sol|terra|luna`. `gpt-6.1-sol` →
  "not supported when using Codex with a ChatGPT account"; `gpt-6-astra` → "requires a newer version
  of Codex". ChatGPT.app bundles codex-cli 0.166.0-alpha, which lists the gpt-6 models.
  Running the old CLI rewrites `~/.codex/models_cache.json` with its own (5.6-only) list.
- A step's checks can leave files (Python `__pycache__`); they are removed before staging, and
  agent and checks run with `PYTHONDONTWRITEBYTECODE=1`.
- One light step on `gpt-5.6-luna low` costs ~60K input tokens (~85% cached), mostly Codex's own prompt.
- Still open: whether `claude -p` stream-json carries rate-limit information (Agent SDK documents
  `rate_limit_event`). Claude support is not built.
