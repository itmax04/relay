# relay — status (2026-11-03, at 6b2950f)

Handover from Claude Code to Codex. Rules and checks are in `AGENTS.md`.

## Phases
| Phase | What | State |
|---|---|---|
| 0 | Context guard hooks (soft 150K / hard 300K), relay-handoff skill, usage reports, Claude status line → quota | done (1e9d9a4) |
| 1 | `relay watch` engine: live tailing, rules, Groq advisor, local API + SSE, advice log with outcomes | done (f51ab78) |
| 2 | SwiftUI menu-bar app (thin client) | working version (9bc171c … 6b2950f) |
| 3 | Dispatcher `relay run` | design agreed (`docs/design/dispatcher.md`), no code |

## Verified
- Python tests 53 OK; `swift test` 13 OK + 2 skipped; `app/scripts/build.sh` builds.
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
4. Phase 3 dispatcher v1, Codex only, per `docs/design/dispatcher.md`. Owner's 8 decisions are
   recorded there. First verify: whether `claude -p --output-format stream-json` carries
   rate-limit info; Codex quota from `turn.completed` / rollout `rate_limits`; exact Codex model
   names per tier from `~/.codex/models_cache.json`. Brainstorm open details with the owner
   before coding.
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
