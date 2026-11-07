"""LLM second opinion, asked only on triggers and answered in a background thread."""
import json
import queue
import re
import threading
import time

from relay import rules

SYSTEM = """You advise a developer who pays for AI coding agents (Claude Code, Codex) with a fixed \
subscription quota. Each model call re-sends the whole session context, so big contexts, retries \
and strong models at high effort burn quota fast. Given one session's metrics, decide the single \
most useful action now:
- new_session: context is big or the cache expired; continue from a handoff in a fresh session
- switch_model: the current work does not need this model tier (or needs a stronger one)
- change_effort: reasoning effort is higher (or lower) than the work needs
- read_narrowly: the agent pulls whole files or long logs into context
- stop_retrying: the agent repeats a failing command; a human should look
- none: nothing worth interrupting the developer for
Suggest model and effort only from the catalog given. Be concrete and short.
Answer with one JSON object only:
{"action": "...", "severity": "info|warn|high", "model": "<slug or null>", \
"effort": "<level or null>", "title": "<= 8 words", "reason": "<= 2 sentences"}"""


def build_messages(ctx):
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(ctx, ensure_ascii=False, default=str)}]


def parse(text, agent, cat):
    """Validated advice fields from a model answer, or None when unusable or "none"."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    action = d.get("action")
    if action not in rules.ACTIONS or action == "none":
        return None
    severity = d.get("severity") if d.get("severity") in rules.SEVERITIES else "info"
    models = {x["slug"]: x for x in cat.get(agent) or []}
    model = d.get("model") if d.get("model") in models else None
    effort = d.get("effort")
    allowed = models[model].get("efforts") if model else sorted({e for x in models.values() for e in x.get("efforts", [])})
    if effort not in (allowed or []):
        effort = None
    title = str(d.get("title") or action.replace("_", " "))[:80]
    reason = str(d.get("reason") or "")[:500]
    return {"action": action, "severity": severity, "title": title, "reason": reason,
            "suggested": {"model": model, "effort": effort} if (model or effort) else None}


class Advisor:
    """Queue of review requests; at most one call in flight, rate-limited per session and per hour."""

    def __init__(self, llm, chain, on_advice, clock=time.time):
        self.llm = llm
        self.chain = chain
        self.on_advice = on_advice
        self.clock = clock
        self.queue = queue.Queue(maxsize=20)
        self.last_by_session = {}
        self.calls = []  # timestamps of calls in the last hour
        self.last_error = None
        self.thread = None

    def wanted(self, session_key):
        now = self.clock()
        if not self.chain:
            return False
        if now - self.last_by_session.get(session_key, 0) < float(self.llm.get("min_interval_per_session") or 600):
            return False
        self.calls = [t for t in self.calls if now - t < 3600]
        return len(self.calls) < int(self.llm.get("max_per_hour") or 20)

    def request(self, session_key, agent, ctx, trigger):
        """Queue a review; returns False when skipped by rate limits."""
        if not self.wanted(session_key):
            return False
        self.last_by_session[session_key] = self.clock()
        self.calls.append(self.clock())
        try:
            self.queue.put_nowait((session_key, agent, ctx, trigger))
        except queue.Full:
            return False
        return True

    def start(self, cat_fn):
        self.cat_fn = cat_fn
        self.thread = threading.Thread(target=self._run, name="relay-advisor", daemon=True)
        self.thread.start()

    def _run(self):
        while True:
            item = self.queue.get()
            if item is None:
                return
            self.handle(*item, cat=self.cat_fn())

    def handle(self, session_key, agent, ctx, trigger, cat):
        try:
            text, label = self.chain.complete(build_messages(ctx))
        except Exception as e:  # noqa: BLE001 - any provider failure means rules only
            self.last_error = str(e)[:300]
            return None
        self.last_error = None
        fields = parse(text, agent, cat)
        if not fields:
            return None
        # Periodic reviews have no rule trigger, but still belong to a session.
        advice = {"rule": "review", "agent": agent, "session": session_key.partition(":")[2],
                  "project": ctx.get("project"), "session_title": ctx.get("session_title")}
        advice.update({k: v for k, v in (trigger or {}).items()
                       if k in ("rule", "agent", "session", "project", "metrics")})
        advice.update(fields)
        advice.update(source=f"llm:{label}", trigger=(trigger or {}).get("id"),
                      key=f"llm:{session_key}:{int(self.clock())}")
        self.on_advice(advice)
        return advice

    def stop(self):
        self.queue.put(None)
