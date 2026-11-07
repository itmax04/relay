# relay — status (2026-11-07, on top of 7155f7c)

Rules and checks are in `AGENTS.md`.

## Third-party skill pack (Claude Code session, 2026-11-07, not committed yet)
- Six small MIT skills copied unchanged into `skills/<name>/` with the upstream `LICENSE`; source
  repo and pinned commit of each in `skills/THIRD_PARTY.md`: `ponytail` (DietrichGebert/ponytail),
  `diagnosing-bugs`, `grilling`, `tdd`, `writing-for-agents` (mattpocock/skills),
  `verification-before-completion` (obra/superpowers). The six `SKILL.md` are ~32 KB, ~40 KB with
  their supporting files (a skill's extra files load only when the skill reads them).
  Owner's choice: a pack of small skills, not one merged skill (a skill loads whole into context).
- `relay install claude|codex` links every `skills/*` with a `SKILL.md` into `~/.claude/skills` /
  `~/.agents/skills`. Only relay links (symlink to `skills/<same name>` of a relay checkout) are
  replaced or removed; a foreign file, directory or link with the same name is skipped and printed.
  `uninstall` removes every relay link in the folder, also for skills no longer shipped.
- `/api/agents`: new `skills_linked` and `skills_missing` (bundled skills without a relay link);
  `skill_linked` / `plan_skill_linked` stay. Settings → Agents shows "Skill pack" and the install
  command when something is missing.
- NOT done: the real `relay install` on the owner's Mac (needs the owner's "yes"); the effect on
  usage is to be measured with `relay usage` for a week before and after the install.
- `tdd` points to `codebase-design` and `code-review` from its repo; they are not in the pack, and in
  Claude Code `code-review` is Claude Code's own built-in skill.

## Stage rule and relay-plan skill (Claude Code session, 2026-11-03, commits 792ec2a + next)
- `relay install claude|codex` writes a managed block (`<!-- relay:plan-rule begin/end -->`) to
  `~/.claude/CLAUDE.md` / `~/.codex/AGENTS.md`: plan stages with a tier each, stop only when the tier
  changes or the context passes `soft_limit`, continue in a new session from the handoff. Models per
  tier: `catalog.STAGE_TIERS` (the only place to change them); the limit: user `soft_limit`.
  `--no-rule` skips the block, uninstall removes it, broken markers are left alone.
  Skill `skills/relay-plan` (examples, no model names) is linked like relay-handoff.
- `/api/agents`: `rule_installed` (block present and current; an outdated block counts as missing,
  rerun install) and `plan_skill_linked`; Settings → Agents shows "Stage rule" / "Plan skill".
- Installed for real by the owner's "yes" on 2026-11-03 (backups `*.relay-backup-20261103-191154`).
  Live Codex check (gpt-5.6-terra, read-only scratch dir): quotes the block, a small question gets a
  plain answer, a multi-stage task gets a plan with tiers after reading the skill.
- NOT verified: stop at a stage boundary and at the context limit in a real project. Changing
  `soft_limit` in Settings does not rewrite the block; rerun `relay install`.
- Open: rule says "Haiku 5.5 low" but the catalog lists no effort levels for Haiku.
- 2026-11-07: every session that ends with work left (stage boundary, context
  limit, finished step with a next one) must end with one line: next stage, tier → model + effort,
  "in a new session", and a concrete prompt (`RULE` in `relay/install.py`, format and examples in
  `skills/relay-plan`, `skills/relay-handoff` points to it). Installed:
  Block rewritten in both agents on the owner's "yes" (backups `*.relay-backup-20261107-120734`).

## Token-skills research (2026-11-07)
- Decision: ABVX `token-efficient-execution` added to the pack; Galvarcus (GPL-3.0), Token Optimizer
  (noncommercial), Caveman, RTK rejected. Full notes: `.relay/token-skills-research.md` (local).

## Phases
| Phase | What | State |
|---|---|---|
| 0 | Context guard hooks (soft 150K / hard 300K), relay-handoff skill, usage reports, Claude status line → quota | done (1e9d9a4) |
| 1 | `relay watch` engine: live tailing, rules, Groq advisor, local API + SSE, advice log with outcomes | done (f51ab78) |
| 2 | SwiftUI menu-bar app (thin client) | working version (9bc171c … 6b2950f) |
| 3 | Dispatcher `relay run`, Codex only | v1 (c44fc75) and v1.1 hardening (7155f7c), see `docs/design/dispatcher.md` |

## Verified
- Python tests 91 OK; `swift test` 21 OK + 2 skipped; `app/scripts/build.sh` builds (2026-11-07).
- Engine on real logs: sessions, context, soft/hard context advice; Codex stale quota ignored.
- App screens Now, Suggestions, Quotas, Report, Settings, popover with live data (screenshots).
- Groq key added from the app (Keychain service `relay.groq`, account `test`);
  `relay advisor check` → all three chain models OK.
- Claude guard hook fires live (Stop asks for a handoff above 150K).
- Continue sheet: hang fixed in 6b2950f (projectRoot looped forever at "/" for cwds with no
  .git/.relay ancestor, e.g. ~/Downloads). The owner clicked the real card button: the sheet opens.

## Not verified yet
1. A real LLM advice from Groq. All advice so far comes from rules. This is the main gap: run
   `relay watch` for a day on real sessions, check `advice.jsonl` (state dir
   `~/.local/state/relay/`) for advisor items, and have the owner mark them Useful/Wrong.
2. Claude quota: needs one reply in an interactive `claude` session (status line writes
   `quota.jsonl`). The app shows "?" and a hint until then.
3. Codex hooks: owner has to trust them in Codex `/hooks` (installed by `relay install codex`).
4. macOS notifications (osascript, high severity only, 1/h per session+rule).
5. In the UI: Useful/Wrong buttons (same client call as a passing live test), "Open in Codex",
   removing a Groq key.
6. A Codex model chip on a card (no active Codex session at the time; unit test only).

## Known small issues
- The main window opens on the desktop Space even when the owner is in a fullscreen app Space,
  so it seems to "disappear". `MenuBarView` calls `openWindow(id: "main")` +
  `NSApp.activate` on every Continue request; consider moving the window to the active Space.
- Terminal launch scripts (`TerminalLauncher` in `app/Sources/RelayApp/Services.swift`) rely on
  the login shell's PATH to find `claude` / `codex`; resolve absolute paths or prepend
  `/opt/homebrew/bin:/usr/local/bin`.
- Advice text is English from the engine (localise by `rule` later). README main text is Russian.

## Next, in order
1. Owner: one message in interactive `claude`; trust hooks in `codex` `/hooks`.
2. Watch for the first real Groq advice (above, item 1); fix whatever the live run shows.
3. The two small issues above (each a small, separate commit).
4. Dispatcher: the v1.1 acceptance run (scratch repo with Copy/Setup/Baseline, a baseline failure
   with 0 Codex calls, detach + resume after `kill`) is not recorded; `~/.local/state/relay/runs/`
   had only two toy runs from 2026-11-03 on 2026-11-07. Then Claude (`claude -p`) as a second agent;
   open: whether its stream-json carries rate-limit info.
5. Later: owner designs the look (`Theme.swift` only); Russian strings in the catalog.

## Facts worth keeping (checked in official docs 2026-11-02/03)
- No hook or MCP server can change an agent's model or effort. Hooks can inject context and block
  a prompt/Stop. Real control only by owning the agent: Claude Agent SDK (setModel, interrupt) or
  Codex app-server (`turn/start` with model+effort). The SDK with claude.ai login is for personal use only.
- Claude quota sources: the CLI status line `rate_limits.{five_hour,seven_day}` (Pro/Max, after the
  first response) and the TS Agent SDK `/usage` (experimental). The desktop app does not run the
  status line.
- Advice delivery costs: the app and notifications cost the agent nothing; advice injected through a
  hook becomes part of the agent's context and counts against its quota.
