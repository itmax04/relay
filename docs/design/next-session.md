# Next session on one click (design, 2026-11-07)

Status: design agreed (strong stage done); implementation not started (standard).
Base: commit e1995a8 (rule "every ending session ends with a recommendation line").

## Goal
When an agent ends a session with work left, it already writes the human line
`<stage>, <tier> → <model> <effort>, in a new session. Prompt: «…»`.
relay picks that up and offers a ready new session (agent, model, effort, prompt, project root);
the owner only confirms with a click. relay never switches a model by itself (AGENTS.md).

## 1. Machine tag (rule + skill)
- After the human line the agent writes one last line, exactly:
  `<!-- relay-next {"model": "<model>", "effort": "<effort>", "prompt": "<prompt>"} -->`
  One-line JSON object, the same prompt as in the human line, model/effort from the Tiers line.
  Same agent as the current session, so no `agent` field.
- `relay/install.py` RULE: one short sentence for the tag (the block is sent with every call).
  `skills/relay-plan/SKILL.md`: the tag in the format section and in every example.
- A changed RULE makes `rule_installed` false until the block is rewritten (see Install).

## 2. Engine (Python, stdlib)
- `relay/live.py`, parse assistant text:
  - Claude: `_feed_claude` assistant branch, text blocks via `_text(content)`.
  - Codex: `response_item` with `type == "message"` and `role == "assistant"`; confirm the payload
    shape on a real transcript under `~/.codex/sessions`.
  - Regex `<!--\s*relay-next\s+(\{.*?\})\s*-->` (re.S), last match wins, `json.loads`, ignore errors;
    keep str `model`, `effort`, `prompt` (prompt required, capped ~2000 chars).
  - `self.next_session = {"model", "effort", "prompt", "ts", "turn": self.turns}`; a later user
    prompt makes it stale (the rule fires only while `turn == s.turns`). Add to `snapshot()`.
- `relay/rules.py`: session rule `next_session` in `SESSION_RULES`:
  - fires while fresh (turn matches, ts within 12 h);
  - `_advice(s, "next", <key stable across restarts, e.g. hash of ts+prompt>, "new_session", "warn",
    "Next session ready: <model> <effort>", reason=<prompt>, model=…, effort=…, prompt=…)`;
    `_advice` gets `prompt=None` → `suggested.prompt`.
  - Model normalization: Claude name / alias / slug → slug via `catalog.claude_models()`
    ("Sonnet 5.5" → `claude-sonnet-5-5`); Codex as is; unknown → raw string.
    Drop effort when the catalog lists the model's efforts and the value is not among them
    (Haiku lists none → dropped).
  - Check `store.AdviceLog.add` dedupe: `_bootstrap` re-reads tails on restart; the same tag must not
    make a second card.
- `relay/notify.py`: also notify for rule `next` (today only severity `high`), still 1/h per
  session+rule. Title "relay: next session ready", message "<model> <effort> — <prompt[:80]>".

## 3. App (Apple frameworks only; visuals only via Theme.swift; strings in Localizable.xcstrings)
- `RelayKit/Models.swift`: `Suggested.prompt: String?` (optional, old data still decodes).
- `RelayKit/Logic.swift` `LaunchCommand.make`: `--effort <level>` for Claude too
  (`claude --help` lists it, CLI 2.1.317).
- `RelayApp/Views/ContinueSheet.swift`:
  - prompt = `suggested?.prompt ?? handoff.continuationPrompt` (shown, copied, launched);
  - effort passed for both agents;
  - launch enabled without a handoff file when the agent gave a prompt (project root still from
    `AppModel.handoff(cwd:)`), keep the "No handoff yet" warning;
  - for rule `next`, the session's own agent button is the default (`.borderedProminent`, Return).
- Cards: `new_session` already opens the Continue sheet; no new screen.

## 4. Tests
- Python: tag from a Claude and a Codex line; bad JSON ignored; last tag wins; stale after a user
  prompt; name → slug; Haiku effort dropped; one advice across a re-read; notifier fires for `next`.
- Swift: `LaunchCommand` with Claude effort; `Suggested` decodes with/without `prompt`;
  `AdviceAction.of` for rule `next` → `.continueInNewSession`.

## 5. Checks
```
python3 -m unittest discover -s tests        # 92 OK before this change
cd app && swift test                          # 21 OK + 2 skipped before
app/scripts/build.sh
```
Live: restart the engine, add a scratch transcript line with the tag (or end a real session),
check the advice via `127.0.0.1:8765` (advice endpoint or SSE) has `suggested.prompt`; relaunch the
app (`pkill -x Relay; open app/build/Relay.app --args --continue <cwd-part>`) and screenshot the
sheet. Do not launch an agent session without the owner's yes.

## Install (after the owner approves the merge)
Hooks and skill links point to `~/Developer/relay`; never run the full `relay install` from a
worktree. Rewrite only the block from the merged main checkout, with the owner's yes:
`cd ~/Developer/relay && python3 -c "from relay import install; [install._put_rule(a, install.TARGETS[a]['instructions']()) for a in ('claude','codex')]"`

## Not in scope
No launch without a click, no model switch inside a running session, no Claude desktop (Code tab)
integration. At the end: a `docs/status.md` section and the test counts in AGENTS.md.
