# relay

A tool that keeps AI coding agents from spending subscription quota on oversized context.
It works the same way for any agent and model: a shared core with a thin adapter for each agent.
Claude Code and Codex are currently supported.

## Why

Every model request sends the entire chat history again. Measurements from the owner's
October 2026 logs showed:

- 74% of requests carried more than 200K tokens of context; large chats averaged 450–550K;
- limiting context to 150K would have avoided resending 66% of the tokens.

The priority is to end sessions at the right time and continue in a new one with a short handoff.

## Components

| Component | What it does |
|---|---|
| `relay usage` | Shows token usage by agent, context size and session, plus the latest quota readings |
| Guard (`SessionStart`, `UserPromptSubmit`, `Stop` hooks) | Warns you and the agent above `soft_limit`. At the end of a turn, asks the agent once to write a handoff. Above `hard_limit`, asks it not to start new work in that session |
| `relay-handoff` skill | Explains how to write a handoff and resume work from it. Uses the Agent Skills format (`SKILL.md`) |
| Skill pack | `ponytail`, `diagnosing-bugs`, `grilling`, `tdd`, `writing-for-agents`, `verification-before-completion`: small third-party MIT skills, copied unchanged from pinned commits. Sources and licenses: [skills/THIRD_PARTY.md](skills/THIRD_PARTY.md) |
| Stage rule and `relay-plan` skill | A block in `~/.claude/CLAUDE.md` and `~/.codex/AGENTS.md`: plan stages with a model tier (strong / standard / light), stop only at a tier change or above `soft_limit`, and continue in a new session from the handoff. Tier models: `relay/catalog.py` (`STAGE_TIERS`) |
| `relay statusline` | Claude Code status line: context size, five-hour and weekly quota windows. Logs each new quota reading |
| `relay watch` | An advisor running alongside your sessions: reads live logs, applies rules, consults an LLM on triggers, serves results through a local API and sends macOS notifications |

The guard never breaks the agent: on any error, it exits silently and records the error in
`~/.local/state/relay/errors.log`.

## Installation

Requires only Python 3.9+ and its standard library. No dependencies.

```bash
~/Developer/relay/bin/relay install claude --autocompact 350000
```

```bash
~/Developer/relay/bin/relay install codex
```

Codex runs new hooks only after you trust them: open Codex and approve them in `/hooks`.

`--autocompact 350000` is a fallback if you ignore the guard's warnings. Claude Code will
compact the history at 350K tokens rather than at 97% of its 1M window.

`relay install` symlinks every skill in `skills/` (directories containing `SKILL.md`:
`relay-handoff`, `relay-plan` and the third-party pack) into `~/.claude/skills` or
`~/.agents/skills`. An existing file, directory or unrelated symlink with the same name is
left in place, with a `skip skill` message. `relay uninstall` removes only relay's symlinks.

`relay install` also installs the stage rule (the block between the `relay:plan-rule` markers);
`--no-rule` skips it. After changing models in `catalog.py` or changing `soft_limit`, run
`relay install` again: it replaces the block in place and preserves the rest of the file.

Uninstall: `relay uninstall claude [--autocompact]`, `relay uninstall codex`. Before each
configuration edit, a backup is saved beside the file (`*.relay-backup-*`).

## Configuration

Override the built-in defaults in either of two files. Values in `<project>/.relay/config.json`
take precedence over `~/.config/relay/config.json`:

```json
{
  "soft_limit": 150000,
  "hard_limit": 300000,
  "handoff_path": ".relay/handoff.md"
}
```

If a project already has a state handoff document (for example, the Lingo SDLC handoff), set
`handoff_path` to it. The skill also looks first for the document specified by the project rules.

## Commands

```bash
relay usage --days 30
```

```bash
relay usage --since 2026-10-01 --agent claude --json
```

```bash
relay context claude <transcript.jsonl>
```

```bash
relay config
```

```bash
relay watch --once
```

```bash
relay advisor check
```

## Advisor: `relay watch`

`relay watch` is a long-running process. It uses no agent tokens: it only reads their logs
(`~/.claude/projects/**.jsonl`, including Claude app sessions, and `~/.codex/sessions/**.jsonl`).
You still choose when to switch models or start a new session.

```bash
relay watch --once
```

Scans active sessions once (modified within `watch_active_hours`, 12 hours by default), prints
sessions and advice, and neither writes to the log nor calls the LLM. Useful for checking setup.

```bash
relay watch
```

Continuous mode: polls logs every two seconds, with the API at `http://127.0.0.1:8765`.
`--no-llm` uses rules only, `--no-notify` disables notifications, and `--port N` changes the port.

### Rules (free and immediate)

| Rule | Trigger | Advice |
|---|---|---|
| `context` | Context above `soft_limit` / `hard_limit`; session idle longer than the cache lifetime | Start a new session from the handoff; leave abandoned sessions alone |
| `cache` | Context above `big_context` (100K), with less than five minutes before cache expiry / cache expired | Send a message now or start fresh |
| `repeat` | The same command fails `repeat_failures` (3) times in a row | Stop the agent and inspect the error yourself |
| `large_read` | One tool response exceeds `large_read_tokens` (20K) without offset/limit | Read narrowly with grep or offset/limit |
| `mechanical` | The last `mechanical_run` (10) calls only read/edit files, while using a strong model or high effort | Use a lower model tier or `medium` effort |
| `quota` | Weekly quota above `quota_warn` / `quota_high` (75/90%), or five-hour window above 90% | Use a lighter model or lower effort |

Cache lifetime is `cache_ttl` (Claude: 3600 seconds, Codex: 600 seconds). Each piece of advice is
issued once; its key is stored in the log so a restart does not repeat it.

### LLM advisor

Runs only on triggers: a new warn/high rule suggestion and every `review_every_calls` (40)
calls in a session with large context; at most once per ten minutes per session and 20 times
per hour. Receives metrics, the latest prompt, error excerpts, file names, quota and the model
catalog. Returns an action, a model and effort from the catalog, a reason and severity.

Model catalog: Codex uses `~/.codex/models_cache.json` (tiers are inferred from descriptions;
override them with `model_tiers` in the config). Claude uses a built-in list dated 2026-11-02,
replaceable with `~/.config/relay/claude_models.json` in the same format.

Provider chain (`llm.chain`): Groq `openai/gpt-oss-120b` → Groq `openai/gpt-oss-20b` →
Groq `qwen/qwen3.8-27b` → Cloudflare Workers AI `@cf/meta/llama-3.3-70b-instruct-fp8-fast` →
rules only. On a 429, the next key for the same provider is used (the key waits for
`retry-after`). On a 5xx, network error or timeout, the provider waits for `provider_cooldown`
(300 seconds). Models unavailable to the account (404 / `model_not_found`) are skipped for a day.

These are the only three chat models available to the free Groq organization (checked
2026-11-03); Lingo uses them too. Groq limits apply per organization and model, so use a
separate Groq organization for relay's key. `relay advisor check` shows what a key can access.
The free 10,000 Workers AI neurons per day are shared across the entire Cloudflare account,
including Lingo.

### Keys

relay does not store keys in files. Sources, in priority order:

| Provider | Environment variables | macOS Keychain |
|---|---|---|
| Groq | `RELAY_GROQ_API_KEYS="k1,k2"` or `RELAY_GROQ_API_KEY` | Service `relay.groq`, one item per key; item account names in `llm.groq_keychain_accounts` (default: `["default"]`) |
| Cloudflare | `RELAY_CF_API_TOKEN` and `RELAY_CF_ACCOUNT_ID` | Service `relay.cloudflare`, item account name is the account ID from `llm.cloudflare_account_id` |

Add a key to Keychain (the command prompts for it, keeping it out of shell history):

```bash
security add-generic-password -s relay.groq -a default -w
```

Use different account names for additional Groq keys, then list them in `~/.config/relay/config.json`:

```bash
security add-generic-password -s relay.groq -a second -w
```

```json
{"llm": {"groq_keychain_accounts": ["default", "second"], "cloudflare_account_id": "<id>"}}
```

```bash
security add-generic-password -s relay.cloudflare -a <account-id> -w
```

Check: sends one tiny request using one key per provider.

```bash
relay advisor check
```

### Local API

Binds to `127.0.0.1` only; requests with an unrecognized `Host` are rejected.
Intended for the future SwiftUI app.

| Request | Response |
|---|---|
| `GET /api/health` | Engine and LLM chain status |
| `GET /api/sessions`, `GET /api/sessions/<id>` | Sessions: context, model, effort, turns, tools, errors and idle time |
| `GET /api/quota` | Latest Claude (`quota.jsonl`) and Codex (`rate_limits`) quota readings |
| `GET /api/catalog` | Models by agent |
| `GET /api/advice?limit=&session=` | Advice, newest first |
| `POST /api/advice/<id>/outcome` | `{"outcome": "useful" \| "wrong" \| null, "note": "..."}`, JSON body only |
| `GET /api/config` | User file, effective settings and defaults |
| `POST /api/config` | Partial settings → `~/.config/relay/config.json`, applied immediately; `null` resets a key, `{}` reloads keys from Keychain |
| `GET /api/usage?days=7` | `relay usage --json` data (cached for two minutes) |
| `GET /api/agents` | Whether each agent has hooks, a status line, the stage rule and skills installed (`skills_linked`, `skills_missing`) |
| `GET /api/runs`, `GET /api/runs/<id>` | Dispatcher runs, newest first |
| `POST /api/runs/<id>/stop`, `POST /api/runs/<id>/approve` | `{}`: stop a run / approve a plan or a `[confirm]` step |
| `GET /api/events` | SSE stream: `session`, `advice`, `quota`, `run` events |

Advice log: `~/.local/state/relay/advice.jsonl` (append-only; ratings are separate
`{"type": "outcome"}` entries). macOS notifications are high severity only, at most once
per hour per session and rule.

`llm.send` controls which session details the LLM advisor receives (`prompt`, `errors`,
`files`, `tools`, all `true` by default). Metrics are always sent while the advisor is enabled.

## Dispatcher: `relay run`

Executes a plan step by step, each step in a new agent process with short context (design:
[docs/design/dispatcher.md](docs/design/dispatcher.md)). Version 1 supports Codex only.

```sh
relay plan "what to do, in two or three sentences"   # a strong model writes .relay/plan.md (read-only repository access)
relay run start                  # waits for plan approval: relay run approve <id> or the app button
relay run start --yes            # the plan is already approved
relay run list | show <id> | stop <id> | approve <id> | remove <id>
```

Plan format: see the beginning of `relay/plan.py`. In brief: `## 1. Title [light|standard|strong]`,
optional `[confirm]` and `[agent: codex]`, one or more `Check: \`command\`` lines and
`Files: paths, directories/`; the remaining text is passed to the agent.

Each step runs `codex exec --json -m <tier-model> -c model_reasoning_effort=<effort> -s workspace-write`
in the worktree `~/.local/state/relay/runs/<id>/worktree` on branch `relay/run-<id>`. The dispatcher
then runs the step's checks, requires a nonempty diff confined to allowed files and creates a
commit. Files generated by the checks (caches, build output) are excluded. On failure, it retries
with the error text (`run.max_retries`, default: 2), then raises the tier, then stops and notifies
you. A model rejection (`model_not_found`, "a newer Codex version is required") stops the run
immediately without retries. Before each step, it checks Codex quota: above `run.quota_switch_at`
(80%) in the five-hour window or `run.quota_week_stop` (98%) for the week, it waits for the window
to reset. It does not push; you merge the branch yourself.

Tier models are configured in `run.tiers.codex`. Codex CLI 0.144.6 (the version on PATH as of
2026-11-03), authenticated through ChatGPT, accepts only `gpt-5.6-*`; `gpt-6*` models require a
newer Codex version. Defaults: strong = `gpt-5.6-sol` xhigh, standard = `gpt-5.6-sol` medium,
light = `gpt-5.6-luna` low. Override the Codex executable with `run.codex_bin`.

## Mac app

`app/` is a native SwiftUI app (menu bar + window), a thin client for `relay watch`. Advice
appears there, outside agent chats. Build: `app/scripts/build.sh --open`.
Details: [app/README.md](app/README.md).

## Adding an agent

An agent needs a two-part adapter:

1. A log reader in `relay/transcripts.py`: converts its log into `Call` records and determines
   the context size of its latest request.
2. An installation target in `relay/install.py`: defines where to write hooks and symlink skills.

If an agent has no hooks, the `relay-handoff` skill still works. You initiate the switch to a
new session yourself.

## Limitations

- Claude Code and Codex log formats are not stable public interfaces. If a format changes,
  the guard stops triggering instead of breaking the agent.
- Rankings in `relay usage` use relative API prices. They do not represent subscription quota
  shares: providers do not publish their subscription quota formulas.
- The Claude Code CLI displays the status line. Whether the Claude app displays it is unverified.
- The advisor only advises: MCP and hooks cannot change the model or effort of a running session.
- Tool response size is estimated as characters / 4. Codex errors are detected from output text
  (`exit code N`, `Error`, `Traceback`) because its tools return free-form text.
- Claude quota is visible only with relay's status line installed (it writes `quota.jsonl`).

## Next

Phase 2, the Mac app (`app/`), is done; the owner will define its visual design through
`app/Sources/RelayApp/Theme.swift`. Phase 3, the `relay run` dispatcher: Codex v1 and v1.1 hardening
are done. Next: Claude (`claude -p`) as a second agent selected by quota, and starting runs from
the app.
