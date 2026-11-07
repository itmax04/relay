"""Free, instant checks over live session state. Each check returns advice dicts with a `key`;
the engine emits a key once, so a check may return the same advice on every pass."""
import hashlib
import time

from relay import catalog

ACTIONS = ("new_session", "switch_model", "change_effort", "read_narrowly", "stop_retrying", "none")
SEVERITIES = ("info", "warn", "high")
HIGH_EFFORTS = ("high", "xhigh", "max", "ultra")


def _k(n):
    return f"{round(n / 1000)}K"


def _advice(s, rule, key, action, severity, title, reason, model=None, effort=None, prompt=None, **metrics):
    return {"key": f"{s.agent}:{s.id}:{rule}:{key}" if s else f"{rule}:{key}", "rule": rule,
            "agent": s.agent if s else None, "session": s.id if s else None, "project": s.project if s else None,
            "session_title": s.title if s else None,
            "title": title, "action": action, "severity": severity, "reason": reason,
            "suggested": ({"model": model, "effort": effort, "prompt": prompt}
                          if (model or effort or prompt) else None), "metrics": metrics}


def context_limits(s, cfg, cat, now):
    hard, soft = int(cfg["hard_limit"]), int(cfg["soft_limit"])
    idle = s.idle(now) or 0
    if s.context >= soft and idle > int((cfg["cache_ttl"] or {}).get(s.agent, 300)):
        # Nobody is working in it now: no point interrupting, but resuming it would be the expensive move.
        return [_advice(s, "context", "dormant", "new_session", "info", f"Don't resume this {_k(s.context)} session",
                        f"It sat idle past the cache lifetime; the next message re-sends {_k(s.context)} tokens "
                        "uncached. Continue from a handoff in a new session instead.", context=s.context,
                        idle_seconds=int(idle))]
    if s.context >= hard:
        return [_advice(s, "context", "hard", "new_session", "high", f"Context {_k(s.context)}: start a new session",
                        f"Every call re-sends {_k(s.context)} tokens, over the {_k(hard)} hard limit. Ask the agent "
                        "for a handoff now and continue in a fresh session.", context=s.context)]
    if s.context >= soft:
        return [_advice(s, "context", "soft", "new_session", "warn", f"Context {_k(s.context)}: plan a new session",
                        f"Every call re-sends {_k(s.context)} tokens, over the {_k(soft)} soft limit. Finish the "
                        "current step, write a handoff and start fresh.", context=s.context)]
    return []


def cache_expiry(s, cfg, cat, now):
    idle = s.idle(now)
    if idle is None or s.context < int(cfg["big_context"]):
        return []
    ttl = int((cfg["cache_ttl"] or {}).get(s.agent, 300))
    left = ttl - idle
    stamp = int(s.last_call_ts or s.last_activity or 0)
    if 0 < left <= int(cfg["cache_warn_seconds"]):
        return [_advice(s, "cache", f"expiring-{stamp}", "new_session", "info",
                        f"Cache expires in ~{max(int(left // 60), 1)} min",
                        f"The {_k(s.context)} prompt cache lapses after {ttl // 60} min idle. Send the next message "
                        "before then, or switch to a fresh session from a handoff.",
                        context=s.context, idle_seconds=int(idle), ttl=ttl)]
    if left <= 0 and idle < 12 * 3600:
        return [_advice(s, "cache", f"expired-{stamp}", "new_session", "warn", "Cache expired on a big context",
                        f"The next message re-sends {_k(s.context)} tokens without cache. If the work is not mid-step, "
                        "start a new session from the handoff instead of continuing here.",
                        context=s.context, idle_seconds=int(idle), ttl=ttl)]
    return []


def repeated_failures(s, cfg, cat, now):
    n = int(cfg["repeat_failures"])
    tools = list(s.recent_tools)
    if not tools or not tools[-1]["error"]:
        return []
    last = tools[-1]
    count = sum(1 for t in tools if t["sig"] == last["sig"] and t["error"])
    if count < n:
        return []
    return [_advice(s, "repeat", f"{last['sig']}:{count // n}", "stop_retrying", "high",
                    f"Same call failed {count} times",
                    f"`{last['label']}` keeps failing; each retry re-sends {_k(s.context)} of context. Stop the agent, "
                    "read the error yourself and give it a narrower instruction.",
                    failures=count, command=last["label"], error=last["excerpt"][:300])]


def large_reads(s, cfg, cat, now):
    limit = int(cfg["large_read_tokens"])
    if not s.large_results:
        return []
    r = s.large_results[-1]
    if r["tokens"] < limit or r["narrow"]:
        return []
    return [_advice(s, "large_read", f"{r['label']}:{r['ts']}", "read_narrowly", "warn",
                    f"Tool output of ~{_k(r['tokens'])} tokens",
                    f"`{r['label']}` added ~{_k(r['tokens'])} tokens that now ride along on every call. Ask for "
                    "grep, offset/limit or a summary instead of whole files and full logs.",
                    tokens=r["tokens"], command=r["label"])]


def mechanical_run(s, cfg, cat, now):
    n = int(cfg["mechanical_run"])
    calls = list(s.recent_calls)[-n:]
    if len(calls) < n or not all(c.get("mechanical") for c in calls):
        return []
    model = catalog.find(cat, s.agent, s.model)
    strong = model is not None and model["tier"] == "strong"
    high = (s.effort or "") in HIGH_EFFORTS
    if not (strong or high):
        return []
    # The window slides with every call, so key on the hour: at most one nudge per hour per setting.
    key = f"{s.model}-{s.effort}-{int((s.last_call_ts or now) // 3600)}"
    if strong:
        cheaper = catalog.cheaper(cat, s.agent, s.model)
        if cheaper:
            effort = "medium" if "medium" in cheaper.get("efforts", []) else None
            return [_advice(s, "mechanical", key, "switch_model", "info", f"{n} mechanical turns on {model['name']}",
                            f"The last {n} calls only read, edit and run simple commands. {cheaper['name']} handles "
                            "this at a fraction of the quota; switch back for design or debugging.",
                            model=cheaper["slug"], effort=effort, turns=n)]
    if high:
        return [_advice(s, "mechanical", key, "change_effort", "info", f"{n} mechanical turns at {s.effort} effort",
                        f"The last {n} calls only read, edit and run simple commands. Medium effort is enough "
                        "for this stretch.", model=s.model, effort="medium", turns=n)]
    return []


def _resolve_next(s, cat, model, effort):
    """Map the agent's model name (alias, display name or slug) to a catalog slug; drop an unsupported effort."""
    raw = (model or "").strip()
    found = None
    for m in cat.get(s.agent) or []:
        names = {m["slug"].lower(), (m.get("name") or "").lower(), (m.get("alias") or "").lower()}
        if raw.lower() in names:
            found = m
            break
    if found is None:
        found = catalog.find(cat, s.agent, raw) if raw else None
    slug = found["slug"] if found else (raw or None)
    if effort and found and found.get("efforts") is not None and effort not in found["efforts"]:
        effort = None
    return slug, effort or None


def next_session(s, cfg, cat, now):
    n = s.next_session
    if not n or n["turn"] != s.turns or now - n["ts"] > 12 * 3600:
        return []
    model, effort = _resolve_next(s, cat, n["model"], n["effort"])
    key = hashlib.sha1(f"{n['ts']}:{n['prompt']}".encode()).hexdigest()[:12]
    label = " ".join(p for p in (model, effort) if p)
    return [_advice(s, "next", key, "new_session", "warn",
                    f"Next session ready: {label}" if label else "Next session ready", n["prompt"],
                    model=model, effort=effort, prompt=n["prompt"])]


def quota(sessions, quotas, cfg, cat, now):
    """Plan-quota pressure, attached to the busiest recently active session of each agent."""
    out = []
    readings = {"claude": _claude_pct(quotas.get("claude")), "codex": _codex_pct(quotas.get("codex"))}
    for agent, (week, five, reset, five_reset) in readings.items():
        if isinstance(reset, (int, float)) and reset < now:
            week = None  # that window already reset; the reading is stale
        if isinstance(five_reset, (int, float)) and five_reset < now:
            five = None
        week_level = None if week is None else \
            "high" if week >= cfg["quota_high"] else "warn" if week >= cfg["quota_warn"] else None
        five_hot = five is not None and five >= cfg["quota_high"]
        if not (week_level or five_hot):
            continue
        level = week_level or "warn"
        active = [s for s in sessions if s.agent == agent and (s.idle(now) or 0) < 3600]
        s = max(active, key=lambda x: x.context, default=None)
        cheaper = catalog.cheaper(cat, agent, s.model) if s else None
        name = agent.capitalize()
        if week_level:
            title = f"{name} weekly quota at {week:.0f}%"
            tail = ". Keep the strong model for hard problems only and use lower effort elsewhere."
        else:
            title = f"{name} 5-hour window at {five:.0f}%"
            tail = (f". It resets at {time.strftime('%H:%M', time.localtime(five_reset))}" if five_reset else "") + \
                "; until then use a lighter model or lower effort, or pause."
        parts = [f"weekly {week:.0f}%" if week is not None else "", f"5-hour {five:.0f}%" if five is not None else ""]
        a = _advice(s, "quota", f"{agent}-{level}-{reset}-{five_reset if five_hot else ''}",
                    "switch_model" if cheaper else "change_effort", level, title,
                    f"{name} limits used: {', '.join(p for p in parts if p)}{tail}",
                    model=cheaper["slug"] if cheaper else None, effort="medium", week=week, five_hour=five)
        a["agent"] = agent
        out.append(a)
    return out


def _claude_pct(q):
    if not isinstance(q, dict):
        return None, None, None, None
    week, five = q.get("seven_day") or {}, q.get("five_hour") or {}
    return week.get("used_percentage"), five.get("used_percentage"), week.get("resets_at"), five.get("resets_at")


def _codex_pct(rl):
    if not isinstance(rl, dict):
        return None, None, None, None
    week, five = rl.get("secondary") or {}, rl.get("primary") or {}
    return week.get("used_percent"), five.get("used_percent"), week.get("resets_at"), five.get("resets_at")


SESSION_RULES = (context_limits, cache_expiry, repeated_failures, large_reads, mechanical_run,
                 next_session)


def evaluate(sessions, quotas, cfg, cat, now=None):
    """quotas: {"claude": last quota.jsonl reading, "codex": latest rate_limits}."""
    now = now or time.time()
    out = []
    for s in sessions:
        for rule in SESSION_RULES:
            out += rule(s, cfg, cat, now)
    return out + quota(sessions, quotas or {}, cfg, cat, now)
