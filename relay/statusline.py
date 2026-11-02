"""`relay statusline`: Claude Code status line that also records plan-quota readings.

Claude Code passes context_window and (for Pro/Max subscribers) rate_limits on stdin. Each
change in the rate-limit readings is appended to <state>/quota.jsonl, which `relay usage`
shows and a later dispatcher can use to pick between subscriptions.
"""
import json
import os
import sys
import time

from relay import config


def _pct(window):
    return window.get("used_percentage") if isinstance(window, dict) else None


def run(stdin=None, stdout=None):
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    try:
        data = json.load(stdin)
    except ValueError:
        return 0
    cfg = config.load((data.get("workspace") or {}).get("current_dir") or data.get("cwd"))
    parts = []
    cw = data.get("context_window") or {}
    usage = cw.get("current_usage") or {}
    ctx = sum(usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    if ctx:
        mark = " !!" if ctx >= cfg["hard_limit"] else " !" if ctx >= cfg["soft_limit"] else ""
        parts.append(f"ctx {round(ctx / 1000)}K{mark}")
    rl = data.get("rate_limits") or {}
    five, week = _pct(rl.get("five_hour")), _pct(rl.get("seven_day"))
    if five is not None:
        parts.append(f"5h {five:.0f}%")
    if week is not None:
        parts.append(f"week {week:.0f}%")
    model = (data.get("model") or {}).get("display_name")
    if model:
        parts.append(model)
    stdout.write(" · ".join(parts))
    if rl:
        _record(rl, data.get("session_id"))
    return 0


def _record(rl, session):
    path = os.path.join(config.state_dir(), "quota.jsonl")
    snapshot = {k: rl[k] for k in ("five_hour", "seven_day") if isinstance(rl.get(k), dict)}
    if not snapshot:
        return
    try:
        with open(path, encoding="utf-8") as f:
            last = json.loads(f.read().splitlines()[-1])
        if {k: last.get(k) for k in snapshot} == snapshot:
            return
    except (OSError, ValueError, IndexError):
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(snapshot, ts=time.strftime("%Y-%m-%dT%H:%M:%S"), session=session)) + "\n")
