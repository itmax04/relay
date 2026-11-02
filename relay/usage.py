"""`relay usage`: where the tokens went, across agents.

Subscription quota is not published as a token formula, so the report ranks sessions by a
proxy weight built from API price ratios (fresh input 1, cache write 1.25, cache read 0.1,
output 5). Treat it as a relative ordering, not as a share of any plan's limit.
"""
import collections
import datetime
import json
import os

from relay import config, transcripts

WEIGHTS = {"fresh": 1.0, "cache_write": 1.25, "cache_read": 0.1, "output": 5.0}
BUCKETS = ((100_000, "<100K"), (200_000, "100-200K"), (400_000, "200-400K"), (700_000, "400-700K"),
           (float("inf"), ">=700K"))


def weight(c):
    return sum(getattr(c, k) * w for k, w in WEIGHTS.items())


def collect(agents, since):
    since_ts = since.isoformat()
    since_mtime = datetime.datetime.combine(since, datetime.time()).timestamp()
    calls, quotas = [], {}
    for agent in agents:
        seen = set()
        for path in sorted(transcripts.discover(agent, since_mtime), key=os.path.getmtime):
            got, reader = transcripts.read_calls(agent, path, seen)
            calls.extend(c for c in got if c.ts[:10] >= since_ts)
            rl = getattr(reader, "rate_limits", None)
            if rl and (agent not in quotas or (rl.get("ts") or "") > (quotas[agent].get("ts") or "")):
                quotas[agent] = rl
    claude_quota = _last_statusline_snapshot()
    if claude_quota:
        quotas["claude"] = claude_quota
    return calls, quotas


def _last_statusline_snapshot():
    path = os.path.join(config.state_dir(), "quota.jsonl")
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        return json.loads(lines[-1]) if lines else None
    except (OSError, ValueError):
        return None


def summarize(calls, cap):
    sessions = collections.OrderedDict()
    for c in calls:
        s = sessions.setdefault((c.agent, c.session), {
            "agent": c.agent, "session": c.session, "project": c.project, "first": c.ts, "last": c.ts,
            "calls": 0, "subagent_calls": 0, "max_context": 0, "context_sum": 0, "output": 0,
            "weight": 0.0, "models": collections.Counter()})
        s["calls"] += 1
        s["subagent_calls"] += c.sidechain
        s["max_context"] = max(s["max_context"], c.context)
        s["context_sum"] += c.context
        s["output"] += c.output
        s["weight"] += weight(c)
        s["models"][c.model] += 1
        s["first"], s["last"] = min(s["first"], c.ts), max(s["last"], c.ts)
    agents = collections.OrderedDict()
    for c in calls:
        a = agents.setdefault(c.agent, collections.Counter())
        a["calls"] += 1
        for k in ("fresh", "cache_write", "cache_read", "output", "context"):
            a[k] += getattr(c, k)
        a["weight"] += weight(c)
        a["over_cap"] += max(c.context - cap, 0)
    buckets = collections.OrderedDict((label, [0, 0]) for _, label in BUCKETS)
    for c in calls:
        label = next(lbl for limit, lbl in BUCKETS if c.context < limit)
        buckets[label][0] += 1
        buckets[label][1] += c.context
    return sessions, agents, buckets


def _m(n):
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}K"


def _as_data(since, cap, per_agent, buckets, ranked, quotas, total_weight):
    for s in ranked:
        s["models"] = dict(s["models"])
        s["share"] = s["weight"] / total_weight
    return {"since": since.isoformat(), "cap": cap, "agents": per_agent, "buckets": buckets,
            "sessions": ranked, "quota": quotas}


def data(agents=transcripts.AGENTS, since=None, cap=150_000, top=10):
    """What `relay usage --json` prints, as a dict (the local API serves it to the app)."""
    calls, quotas = collect(agents, since)
    sessions, per_agent, buckets = summarize(calls, cap)
    total_weight = sum(a["weight"] for a in per_agent.values()) or 1.0
    ranked = sorted(sessions.values(), key=lambda s: -s["weight"])[:top]
    return json.loads(json.dumps(_as_data(since, cap, per_agent, buckets, ranked, quotas, total_weight),
                                 default=float))


def report(agents=transcripts.AGENTS, since=None, cap=150_000, top=10, as_json=False):
    since = since or (datetime.date.today() - datetime.timedelta(days=30))
    calls, quotas = collect(agents, since)
    sessions, per_agent, buckets = summarize(calls, cap)
    total_weight = sum(a["weight"] for a in per_agent.values()) or 1.0
    total_ctx = sum(b[1] for b in buckets.values()) or 1
    ranked = sorted(sessions.values(), key=lambda s: -s["weight"])

    if as_json:
        print(json.dumps(_as_data(since, cap, per_agent, buckets, ranked, quotas, total_weight),
                         ensure_ascii=False, indent=1, default=float))
        return 0

    print(f"relay usage since {since.isoformat()} ({len(calls)} model calls, {len(sessions)} sessions)\n")
    for agent, a in per_agent.items():
        print(f"{agent:7} calls={a['calls']:<6} fresh={_m(a['fresh'])} cache_write={_m(a['cache_write'])} "
              f"cache_read={_m(a['cache_read'])} output={_m(a['output'])}")
        print(f"{'':7} if context were capped at {_m(cap)}: {_m(a['over_cap'])} of {_m(a['context'])} "
              f"re-sent tokens ({a['over_cap'] / (a['context'] or 1):.0%}) would not be sent")
    print("\ncontext size of each call      calls   share of all re-sent context")
    for label, (n, ctx) in buckets.items():
        print(f"  {label:10} {n:>23}   {ctx / total_ctx:.0%}")
    print(f"\ntop {top} sessions by proxy weight (API price ratios, not plan quota):")
    for s in ranked[:top]:
        avg = s["context_sum"] / max(s["calls"], 1)
        model = ", ".join(m for m, _ in s["models"].most_common(2))
        print(f"  {s['weight'] / total_weight:5.1%}  {s['first'][:10]}..{s['last'][5:10]}  {s['agent']:6} "
              f"{s['project'][-28:]:28}  calls={s['calls']:<5} max={_m(s['max_context']):>6} "
              f"avg={_m(avg):>5}  {model}")
    if quotas:
        print("\nlatest quota readings:")
        for agent, q in quotas.items():
            print(f"  {agent:7} {_quota_line(q)}")
    return 0


def _quota_line(q):
    parts = []
    for key, label in (("primary", "5h"), ("secondary", "week"), ("five_hour", "5h"), ("seven_day", "week")):
        w = q.get(key)
        if isinstance(w, dict):
            pct = w.get("used_percent", w.get("used_percentage"))
            if pct is not None:
                parts.append(f"{label} {pct:.0f}%")
    return ", ".join(parts) + f"  (as of {str(q.get('ts', '?'))[:16]})"
