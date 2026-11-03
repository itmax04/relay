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

## v1.1 — hardening for a large real project (proposed 2026-11-03, Codex only)

The owner wants to test on a large real project right away, on Codex, without a small pilot first.
Audit of v1 (`relay/dispatch.py` at c44fc75) found these risks; each item names the fix. Defaults in
`config.DEFAULTS["run"]`; nothing here adds a dependency.

1. **Worktree lacks ignored files and installed deps** (`.env`, `node_modules`, local xcconfig,
   secrets). Checks then fail on every attempt and the agent is blamed: up to 9 Codex runs per step.
   Fix: plan preamble fields, parsed by `plan.py` before the first `##` step:
   - `Copy: <paths>` — ignored files/dirs copied from the main checkout into the worktree after
     `git worktree add` (missing source → run fails before any agent call);
   - `Setup: <command>` (repeatable) — run once in the worktree after copying (e.g. `npm ci`);
   - `Baseline: <command>` (repeatable) — must exit 0 on the untouched worktree (e.g. the build or
     the existing test suite). Failure → run `failed` with the output, zero agent calls.
   Setup/baseline output goes to `runs/<id>/setup.txt`. `DRAFT_PROMPT` asks for these fields.
2. **Environment failures counted as the agent's**. A check exiting 126/127 or printing
   `command not found` / `No such file or directory` for the command itself → stop the run
   (`failed`, "environment: …"), no retry.
3. **Quota burn on hopeless retries**. Default `max_retries` 2 → 1. When two consecutive attempts at
   one tier end with the same failure fingerprint (result kind + failing check commands + last 20
   lines of output, whitespace-normalised), skip the rest of that tier. Per-run cap
   `max_attempts` (default 30) → `stopped`.
4. **Quota exhausted mid-step stops the run.** Instead: read `codex_rate_limits` of that thread (or
   `watch.read_codex_quota()`); if a `resets_at` is known, wait at gate `quota` until then and repeat
   the attempt without counting it; unknown reset → `stopped` as now.
5. **Disk.** The data volume had 13 GB free (94% used) on 2026-11-03; a SwiftPM worktree build
   is ~700 MB. `min_free_gb` (default 5): checked before `worktree add` and before each attempt;
   below it → `stopped` with the free space in the message. `cleanup` (default `on_done`): a `done`
   run removes its worktree (the branch keeps the commits); failed/stopped runs keep it for inspection.
6. **A multi-hour run dies with the terminal or Mac sleep.**
   - `/usr/bin/caffeinate -i -w <dispatcher pid>` started with the run (no-op if missing).
   - `relay run start --detach`: re-executes itself in a new session with stdout to
     `runs/<id>/dispatcher.log`, prints the id and exits.
   - `relay run resume <id>` (also `POST /api/runs/<id>/resume` and a Resume button on the Runs
     screen for `failed`/`stopped` runs): same worktree and branch; `git reset --hard` +
     `git clean -fd` (not `-x`, so copied/ignored setup files stay) to the last step commit; skips
     `done` steps; plan approval is not asked again; attempts history is kept.
7. **Preflight** (`relay run check [--plan]`, also run at the start of `start`, before the
   approval wait): codex binary and version; `codex login status`; every tier model present in
   `~/.codex/models_cache.json` (no API call, no quota); plan parses; steps without `Check:` or
   `Files:` listed as warnings; `Copy:` sources exist; free disk; git repo with a commit; uncommitted
   changes in the project noted. Any error → exit 1 before a worktree or an agent run exists.

Not in v1.1: Claude as an agent, weekly quota projection, starting a new run from the app.
Acceptance: python + swift suites pass with new tests per item (fake codex as in
`tests/test_dispatch.py`); one real run on a scratch repo exercising Copy/Setup/Baseline, a
baseline failure (0 Codex calls), detach + resume after `kill` of the dispatcher.
