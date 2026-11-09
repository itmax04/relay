"""Context guard: one hook handler for every supported agent.

Claude Code and Codex share the hook protocol this module relies on: JSON on stdin with
hook_event_name, session_id, transcript_path and cwd; JSON on stdout with systemMessage
(shown to the user), hookSpecificOutput.additionalContext (added for the model) and, on Stop,
decision/reason (the agent continues with the reason as its next instruction).

The guard never fails the agent: any error ends in exit 0 with no output.
"""
import json
import os
import sys
import time
import traceback

from relay import config, transcripts

SKILL = "relay-handoff"


def _k(tokens):
    return f"{round(tokens / 1000)}K"


def _state_path(agent, session):
    safe = "".join(ch for ch in str(session) if ch.isalnum() or ch in "-_.")[:120] or "unknown"
    return os.path.join(config.state_dir(), "sessions", f"{agent}-{safe}.json")


def _load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(path, state):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f)


def _handoff_note(cfg):
    path = os.path.join(cfg["project_root"], cfg["handoff_path"])
    if not os.path.isfile(path):
        return None
    hours = (time.time() - os.path.getmtime(path)) / 3600
    age = f"{hours:.0f} h" if hours >= 1 else "<1 h"
    return (f"relay: a handoff exists at {cfg['handoff_path']} (updated {age} ago). If the user is "
            f"continuing earlier work, read it first and verify it against the repository instead "
            f"of re-exploring the project from scratch.")


def decide(agent, payload, cfg, state):
    """Return (output dict or None, new state). Pure apart from reading the transcript/handoff."""
    event = payload.get("hook_event_name")
    soft, hard = int(cfg["soft_limit"]), int(cfg["hard_limit"])
    ctx = payload.get("context_tokens")  # Claude Code sends this on SessionStart resume/fork
    if not isinstance(ctx, int) or ctx <= 0:
        ctx = transcripts.current_context(agent, payload.get("transcript_path"))
    out = {}
    extra = []

    if event == "SessionStart":
        if payload.get("source") in ("clear", "compact"):
            state.pop("stop_soft", None)
            state.pop("stop_hard", None)
        note = _handoff_note(cfg)
        if note:
            extra.append(note)
        if ctx and ctx >= soft and payload.get("source") in ("resume", "fork"):
            out["systemMessage"] = (f"relay: this session already has ~{_k(ctx)} tokens of context, resent "
                                    f"on every turn. Starting a new session from a handoff costs less.")
            extra.append(f"relay: this resumed session already carries ~{_k(ctx)} tokens of context "
                         f"(soft limit {_k(soft)}). Suggest the user continue in a fresh session from "
                         f"the handoff ({SKILL} skill) rather than here.")

    elif event == "UserPromptSubmit" and ctx and ctx >= soft:
        if ctx >= hard:
            out["systemMessage"] = (f"relay: context ~{_k(ctx)} > hard limit {_k(hard)}. "
                                    f"The agent will update the handoff; continue in a new session.")
            extra.append(f"relay: context is ~{_k(ctx)} tokens, above the hard limit of {_k(hard)}. "
                         f"Unless the user explicitly insists on continuing here, do not start new "
                         f"work: update the handoff with the {SKILL} skill, then tell the user to "
                         f"continue in a new session. Answer short questions briefly.")
        else:
            out["systemMessage"] = (f"relay: context ~{_k(ctx)} > {_k(soft)}. After this step, "
                                    f"start a new session from a handoff.")
            extra.append(f"relay: context is ~{_k(ctx)} tokens (soft limit {_k(soft)}). Finish the "
                         f"current step, then at that boundary update the handoff with the {SKILL} "
                         f"skill and suggest a new session. Read narrowly: only the files this step needs.")

    elif event == "Stop" and ctx and ctx >= soft and not payload.get("stop_hook_active"):
        level = "hard" if ctx >= hard else "soft"
        if not state.get(f"stop_{level}"):
            state[f"stop_{level}"] = int(time.time())
            if level == "hard":
                state["stop_soft"] = state.get("stop_soft") or state[f"stop_{level}"]
            out["decision"] = "block"
            out["reason"] = (f"relay: context is ~{_k(ctx)} tokens (limit {_k(soft if level == 'soft' else hard)}). "
                             f"Before ending this turn, update the handoff using the {SKILL} skill "
                             f"(skip only if you updated it during this turn and nothing changed since). "
                             f"Then end with one short line telling the user, in their language, where "
                             f"the handoff is and that the next step should start in a new session.")
            out["systemMessage"] = f"relay: context ~{_k(ctx)}; the agent is writing a handoff before a new session."

    if extra:
        out["hookSpecificOutput"] = {"hookEventName": event, "additionalContext": "\n".join(extra)}
    return (out or None), state


def run_hook(agent, stdin=None, stdout=None):
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    try:
        payload = json.load(stdin)
        if not isinstance(payload, dict) or agent not in transcripts.AGENTS:
            return 0
        cfg = config.load(payload.get("cwd"))
        state_path = _state_path(agent, payload.get("session_id"))
        state = _load_state(state_path)
        before = dict(state)
        out, state = decide(agent, payload, cfg, state)
        if state != before:
            _save_state(state_path, state)
        if out:
            stdout.write(json.dumps(out, ensure_ascii=False))
    except Exception:  # never break the agent because of the guard
        try:
            os.makedirs(config.state_dir(), exist_ok=True)
            with open(os.path.join(config.state_dir(), "errors.log"), "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} hook {agent}\n{traceback.format_exc()}\n")
        except OSError:
            pass
    return 0
