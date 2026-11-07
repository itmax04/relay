# AGENTS.md — relay

relay is the owner's personal tool that keeps AI coding agents (Claude Code on a Pro plan, Codex on
ChatGPT) from burning quota on oversized contexts. It watches their transcripts, advises when to
change model/effort or start a new session, and (phase 3, not built) will run plans step by step.

Start every session by reading `docs/status.md` (what works, what is unverified, what is next).
Then read only the files the current step needs.

## Talking to the owner
- Write to the owner in Russian. Code, comments, commit messages and UI strings are English.
- Ask before installing anything (brew, npm, pip, tools). Ask before outward-facing actions.
- Report honestly: what you ran, what passed, what you did not verify.

## Owner decisions (do not re-litigate)
- Advice only: relay never switches an agent's model by itself. Switching is the owner's click
  (the app copies `/model …` or opens a new session in Terminal). Phase 3 may pick models only for
  processes it starts itself.
- No third-party dependencies: Python engine is stdlib only; the Swift app uses only Apple frameworks.
- All visual values (colors, fonts, spacing, radii, sizes, SF Symbols) live in
  `app/Sources/RelayApp/Theme.swift`. Plain system styling for now; the owner designs later.
  Do not propose DESIGN.md options for the app.
- English UI via the String Catalog (`Localizable.xcstrings`); Russian comes later.
- Groq key pools across accounts are allowed (owner accepted the AUP risk). The advisor may see
  metrics, prompt text, error excerpts and file names.
- Repo: `~/Developer/relay`, branch `main`, remote `origin` = private https://github.com/itmax04/relay.
  Commit on `main` and push only when the owner asks. Cloud environments do not fit: relay needs
  this Mac (transcripts, running engine, Xcode, Keychain), so work locally.

## Layout
- `relay/` — Python engine (stdlib). `bin/relay` is the CLI entry (`relay watch`, `relay advisor check`,
  `relay install claude|codex`, `relay hook …`, usage reports).
  Key modules: `live.py` (tailing + per-session state), `rules.py`, `advisor.py` + `providers.py`
  (Groq → Cloudflare chain), `server.py` (127.0.0.1:8765 JSON + SSE), `watch.py` (Engine),
  `guard.py` (context hooks), `statusline.py` (Claude quota → quota.jsonl), `config.py`, `catalog.py`.
- `app/` — SwiftUI menu-bar app, thin client of the engine. `RelayKit` (models, API client, SSE,
  logic; unit-tested) and `RelayApp` (views, `AppModel`, services). See `app/README.md`.
- `skills/` — skills `relay install` links into Claude Code and Codex: relay's own `relay-handoff`
  and `relay-plan`, plus a vendored third-party pack (do not edit; sources in `skills/THIRD_PARTY.md`).
- `docs/design/dispatcher.md` — agreed phase-3 design. `docs/status.md` — current state.
- `.relay/` is gitignored (local handoff notes).

## Checks before you say "done"
```
python3 -m unittest discover -s tests        # 100 tests, all OK (2026-11-07)
cd app && swift test                          # 20 OK + 2 skipped (live tests need RELAY_LIVE=1)
app/scripts/build.sh                          # builds app/build/Relay.app (ad-hoc signed)
```
- Relaunching the app: quit it first (`pkill -x Relay`), because `open --args` does not reach a
  running instance. Then `open app/build/Relay.app --args --show now`.
- Launch flags for checks: `--show <now|suggestions|quotas|report|settings>`, `--popover`,
  `--continue [cwd-part]` (opens the Continue sheet for the newest session whose cwd contains it).
- Screenshots: `swift app/scripts/windows.swift` lists window ids, `screencapture -x -o -l <id> out.png`.
  The owner often works in a fullscreen app Space, so the relay window is "not on screen" and
  Accessibility sees no windows; `screencapture -l` still captures it.
- A hang in the app: `sample Relay 3 -file out.txt` and read the main thread.
- The engine: `curl -s 127.0.0.1:8765/api/health`. Restart: kill the pid from health, the app
  restarts `relay watch` (log `~/.local/state/relay/watch.log`).

## Environment facts
- macOS 15.7, Xcode 26.3. Python for the engine: any 3.9+ (python.org 3.13 has no CA bundle;
  `providers.py` falls back to `/etc/ssl/cert.pem`). Groq's Cloudflare rejects urllib's default
  User-Agent, so requests send `User-Agent: relay`.
- Groq key (account `test`) sees only: openai/gpt-oss-120b, openai/gpt-oss-20b, qwen/qwen3.8-27b
  (+ allam-2-7b, guard/whisper models). Advisor chain = these three, gpt-oss with `reasoning_effort: low`.
- Claude CLI 2.1.317 at `/opt/homebrew/bin/claude`, logged in (claude.ai, Pro). codex-cli 0.144.x,
  logged in via ChatGPT.
