# relay for Mac

Native SwiftUI menu-bar app and window for `relay watch`. It is a thin client of the engine's local
API (`http://127.0.0.1:8765`, see the main README): advice is shown here and never injected into
agent chats, so it costs no agent tokens. Model and effort switching stays the owner's click; the
app only copies commands or opens a new terminal session.

## Build and run

Requirements: macOS 15, Xcode 26 command-line tools, the relay checkout at `~/Developer/relay`
(change it in Settings → Engine → Repository). No third-party dependencies.

```bash
cd app
swift test                 # RelayKit decoding / logic tests (live engine tests are skipped)
scripts/build.sh           # → build/Relay.app (release, String Catalog compiled, ad-hoc signed)
scripts/build.sh --open    # build and launch
```

Copy `build/Relay.app` to `/Applications` to keep it; add it to Login Items to start it with the Mac.
The app has no Dock icon (`LSUIElement`); it lives in the menu bar. For development, `open Package.swift`
opens the package in Xcode.

On launch the app checks `GET /api/health`; if the engine is not running it starts
`bin/relay watch` detached (it keeps running when the app quits), logging to
`~/.local/state/relay/watch.log`. While the engine is down the menu-bar icon is a crossed bolt and
every screen shows a "Start relay watch" banner.

Live round-trip tests against the running engine (they change and then restore one advice
outcome and one setting):

```bash
RELAY_LIVE=1 swift test --filter LiveEngineTests
```

Screenshots of a screen plus the popover content (needs Screen Recording permission):

```bash
scripts/screenshot.sh suggestions /tmp/relay-shots
```

Launch flags used by that script: `--show <now|suggestions|quotas|report|settings>`, `--popover`
(popover content in a window), `--continue` (open the Continue sheet for the newest session).

## Layout

| Path | What |
| --- | --- |
| `Sources/RelayKit/` | API models (`Models.swift`, `Config.swift`), `APIClient` + SSE parser, pure logic: token formatting, quota staleness and weekly forecast, handoff location, launch commands, advice → action |
| `Sources/RelayApp/Theme.swift` | **every visual value**: spacing, radii, sizes, fonts, colors, SF Symbols |
| `Sources/RelayApp/AppModel.swift` | state mirrored from the API; SSE + 10 s poll; engine start/restart; settings and feedback writes |
| `Sources/RelayApp/Services.swift` | Groq keys in Keychain, engine launcher, Terminal launcher, clipboard |
| `Sources/RelayApp/Views/` | menu-bar popover, main window (Now, Suggestions, Quotas, Report, Settings), Continue sheet |
| `Sources/RelayApp/Resources/Localizable.xcstrings` | String Catalog (English source) |
| `Tests/RelayKitTests/` | decoding tests on anonymised real engine responses; opt-in live tests |

## Design

Styling is deliberately plain macOS (system colors, fonts, materials, SF Symbols) until the owner's
own design. Screens reference only `Theme.*` names, so a new design changes `Theme.swift` (and,
for new shapes, `cardStyle`) without touching screens.

## Localisation

UI strings are English source keys in `Localizable.xcstrings`. `build.sh` extracts them from the
compiler (`-emit-localized-strings`) and merges them with `xcstringstool sync`, then compiles the
catalog into the bundle. To add Russian: add translations for `ru` in the catalog (Xcode's catalog
editor or by hand), add `ru` to `CFBundleLocalizations` in `Info.plist`, rebuild. Advice titles and
reasons come from the engine in English; the app could localise them by `rule` later.

## Settings and keys

- Limits and other values are written through `POST /api/config`; the engine owns
  `~/.config/relay/config.json` and applies changes without a restart.
- Groq keys: Settings → Advisor. Each key is one Keychain item, service `relay.groq`, account =
  the name you give it; the app lists accounts but never reads a key back. The item's access list
  holds the app and `/usr/bin/security`, which the engine uses to read it. After adding or removing
  a key the app updates `llm.groq_keychain_accounts` and the engine reloads its chain. Which keys
  to add, and rotating them, is the owner's decision.
- "Data sent to the advisor" toggles map to `llm.send`.
- Agent status (hooks, handoff skill, status line) is read by the engine from `~/.claude/settings.json`
  and `~/.codex/hooks.json`. The app shows the `relay install <agent>` command; it does not edit
  agent configs itself.

## Not in this phase

Workflows, runs, tasks, artifacts, agent rosters and running steps are phase 3 (the dispatcher).
No dollar costs (subscriptions: quota % and tokens only), no system resource meters, no logos,
no `/model` action for Codex (Codex is switched in its own picker; the app copies the model name).
