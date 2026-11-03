"""`relay watch`: tail live sessions, apply rules, ask the LLM advisor on triggers, serve the API."""
import datetime
import glob
import io
import json
import os
import sys
import threading
import time

from relay import advisor, catalog, config, dispatch, install, live, notify, providers, rules, server, store, usage

POLL_SECONDS = 2
SCAN_SECONDS = 30
LLM_RULES = ("context", "cache", "repeat", "large_read", "mechanical", "quota")


def read_claude_quota(path):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(f.tell() - 4096, 0))
            last = f.read().splitlines()[-1]
        return json.loads(last)
    except (OSError, ValueError, IndexError):
        return None


def quota_state(reading, windows, now, source_ready=True):
    """How far a plan-quota reading can be trusted, so an unknown figure is never shown as a number.

    "ok": at least one window has not reset yet. "stale": every window already reset, so the
    current figure is unknown. "no_reading": nothing recorded yet. "no_source": nothing can record
    it (the relay status line is not installed in Claude Code).
    """
    if not isinstance(reading, dict):
        return "no_reading" if source_ready else "no_source"
    resets = [(reading.get(w) or {}).get("resets_at") for w in windows if isinstance(reading.get(w), dict)]
    if resets and all(isinstance(r, (int, float)) and r < now for r in resets):
        return "stale"
    return "ok" if resets else "no_reading"


def read_codex_quota():
    newest = max(live.transcripts.discover("codex", time.time() - 7 * 86400), key=os.path.getmtime, default=None)
    if not newest:
        return None
    s = live.Session("codex", newest)
    size = os.path.getsize(newest)
    live.Tailer()._read(s, max(size - (1 << 20), 0), size, skip_first=size > (1 << 20))
    return s.rate_limits


class Engine:
    def __init__(self, cfg, use_llm=True, use_notify=True, tailer=None, chain=None, notifier=None, out=None,
                 log=None):
        self.cfg = cfg
        self.out = out or sys.stdout
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.tailer = tailer or live.Tailer(float(cfg["watch_active_hours"]))
        self.log = log or store.AdviceLog(os.path.join(config.state_dir(), "advice.jsonl"))
        self.hub = server.Hub()
        self.notifier = notifier or notify.Notifier(use_notify and bool(cfg.get("notify")))
        llm = cfg.get("llm") or {}
        if chain is None and use_llm and llm.get("enabled"):
            chain = providers.Chain(llm)
        self.chain = chain
        self.use_llm = use_llm
        self.use_notify = use_notify
        self._usage_cache = {}
        self.port = None
        self.advisor = advisor.Advisor(llm, chain, self._from_llm)
        self.cat = catalog.load(cfg)
        self.claude_quota = None
        self.codex_quota = None  # from the newest rollout, for when no Codex session is active
        # Claude plan usage is documented only in Claude CLI's status-line input (rate_limits); the
        # desktop app does not run the status line, so readings appear only after a CLI turn.
        self.claude_statusline = True
        self.quota_path = os.path.join(config.state_dir(), "quota.jsonl")
        self.reviewed_at = {}  # session key -> session.calls at the last LLM review
        self.last_scan = 0.0
        self.started = time.time()
        self.run_stamps = {}  # run id -> run.json mtime, to publish "run" events on change

    # --- loop ----------------------------------------------------------------------------------
    def tick(self, now=None):
        """One pass: new files, new lines, rules. Returns advice emitted in this pass."""
        now = now or time.time()
        with self.lock:
            if now - self.last_scan >= SCAN_SECONDS:
                for s in self.tailer.scan(now):
                    self.hub.publish("session", s.snapshot(now))
                self.last_scan = now
                self.cat = catalog.load(self.cfg)
                self.codex_quota = read_codex_quota()
                self.claude_statusline = bool(install.status("claude").get("statusline"))
            for s in self.tailer.poll():
                self.hub.publish("session", s.snapshot(now))
            self._poll_runs()
            q = read_claude_quota(self.quota_path)
            if q != self.claude_quota:
                self.claude_quota = q
                self.hub.publish("quota", self.quota())
            sessions = list(self.tailer.sessions.values())
            emitted = []
            for a in rules.evaluate(sessions, self.quota(), self.cfg, self.cat, now):
                d = self._emit(a)
                if d:
                    emitted.append(d)
                    if d["severity"] in ("warn", "high") and d["rule"] in LLM_RULES:
                        self._review(self._session_for(d), d)
            big = int(self.cfg["big_context"])
            every = int((self.cfg.get("llm") or {}).get("review_every_calls") or 40)
            for s in sessions:
                key = f"{s.agent}:{s.id}"
                if s.context >= big and s.calls - self.reviewed_at.get(key, 0) >= every:
                    self._review(s, None)
            return emitted

    def _poll_runs(self):
        for path in glob.glob(os.path.join(dispatch.runs_dir(), "*", "run.json")):
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            rid = os.path.basename(os.path.dirname(path))
            if self.run_stamps.get(rid) != mtime:
                self.run_stamps[rid] = mtime
                r = dispatch.load(rid)
                if r:
                    self.hub.publish("run", r)

    def runs(self):
        return dispatch.list_runs()

    def run_control(self, rid, action):
        if action == "stop":
            return {"ok": dispatch.request_stop(rid)}
        gate = dispatch.approve(rid)
        return {"ok": bool(gate), "gate": gate}

    def _emit(self, advice):
        d = self.log.add(advice)
        if d:
            self.hub.publish("advice", d)
            self.notifier.maybe(d)
            self._print(d)
        return d

    def _from_llm(self, advice):
        with self.lock:
            self._emit(advice)

    def _print(self, d):
        sugg = d.get("suggested") or {}
        extra = " → " + " ".join(x for x in (sugg.get("model"), sugg.get("effort")) if x) if sugg else ""
        print(f"[{d['ts'][11:19]}] {d['severity'].upper():4} {d.get('agent') or '-'} "
              f"{(d.get('session_title') or d.get('project') or '-')[:34]}: {d['title']}{extra} ({d.get('source')})", file=self.out, flush=True)

    def _session_for(self, advice):
        for s in self.tailer.sessions.values():
            if s.id == advice.get("session") and s.agent == advice.get("agent"):
                return s
        return None

    def _review(self, s, trigger):
        if s is None:
            return False
        key = f"{s.agent}:{s.id}"
        if self.advisor.request(key, s.agent, self.llm_context(s, trigger), trigger):
            self.reviewed_at[key] = s.calls
            return True
        return False

    def llm_context(self, s, trigger):
        """What the advisor model sees: metrics, the last prompt, errors, files, catalog, quota."""
        snap = s.snapshot()
        send = (self.cfg.get("llm") or {}).get("send") or {}
        allowed = {k: send.get(k, True) is not False for k in ("prompt", "errors", "files", "tools")}
        ctx = {
            "agent": s.agent, "project": s.project, "model": s.model, "effort": s.effort,
            "context_tokens": s.context, "context_window": s.context_window, "peak_context": s.peak_context,
            "soft_limit": self.cfg["soft_limit"], "hard_limit": self.cfg["hard_limit"],
            "idle_seconds": snap["idle_seconds"], "cache_ttl_seconds": (self.cfg["cache_ttl"] or {}).get(s.agent),
            "model_calls_seen": s.calls, "user_turns_seen": s.turns, "tool_calls": s.tool_calls,
            "last_user_prompt": s.last_prompt[:1500],
            "recent_tools": [t["label"] + (" [FAILED]" if t["error"] else "") for t in list(s.recent_tools)[-12:]],
            "recent_errors": [t["excerpt"][:300] for t in s.recent_tools if t["error"]][-3:],
            "recent_files": list(dict.fromkeys(reversed(s.files)))[:10],
            "large_tool_outputs": [{"what": r["label"], "tokens": r["tokens"]} for r in list(s.large_results)[-3:]],
            "mechanical_calls_in_last_10": sum(1 for c in list(s.recent_calls)[-10:] if c.get("mechanical")),
            "quota": self.quota().get(s.agent),
            "catalog": [{k: m.get(k) for k in ("slug", "tier", "efforts")} for m in self.cat.get(s.agent) or []],
            "rule_triggered": {k: (trigger or {}).get(k) for k in ("rule", "title", "reason")} if trigger else None,
        }
        for key, fields in (("prompt", ("last_user_prompt",)), ("errors", ("recent_errors",)),
                            ("files", ("recent_files",)), ("tools", ("recent_tools", "large_tool_outputs"))):
            if not allowed[key]:
                for f in fields:
                    ctx.pop(f)
        return ctx

    def run(self, port=None):
        port = port or int(self.cfg["api_port"])
        self.port = port
        httpd = server.serve(self, port)
        self.advisor.start(lambda: self.cat)
        chain = ", ".join(e.label for e in self.chain.entries) if self.chain else "off (rules only)"
        print(f"relay watch: http://127.0.0.1:{port}/api/health · LLM: {chain}", file=self.out, flush=True)
        try:
            while not self.stopping.is_set():
                self.tick()
                self.stopping.wait(POLL_SECONDS)
        except KeyboardInterrupt:
            pass
        finally:
            self.stopping.set()
            self.advisor.stop()
            httpd.shutdown()
        return 0

    # --- API views -----------------------------------------------------------------------------
    def sessions(self):
        now = time.time()
        with self.lock:
            snaps = [s.snapshot(now) for s in self.tailer.sessions.values()]
        return sorted(snaps, key=lambda x: x["last_activity"] or 0, reverse=True)

    def session(self, sid):
        return next((s for s in self.sessions() if s["id"] == sid), None)

    def quota(self):
        codex = self.codex_quota
        for s in self.tailer.sessions.values():
            rl = s.rate_limits
            if rl and (codex is None or (rl.get("ts") or "") > (codex.get("ts") or "")):
                codex = rl
        now = time.time()
        return {"claude": self.claude_quota, "codex": codex,
                "status": {"claude": {"state": quota_state(self.claude_quota, ("five_hour", "seven_day"), now,
                                                           self.claude_statusline),
                                      "source": "statusline"},
                           "codex": {"state": quota_state(codex, ("primary", "secondary"), now),
                                     "source": "rollout"}}}

    def catalog(self):
        return self.cat

    def config_view(self):
        with self.lock:
            effective = {k: v for k, v in self.cfg.items() if k != "project_root"}
        return {"path": config.user_path(), "user": config.load_user(), "effective": effective,
                "defaults": config.DEFAULTS}

    def update_config(self, patch):
        """Write user settings and apply them; an empty patch just reloads (e.g. new Keychain keys)."""
        if patch:
            config.save_user(patch)
        self.apply(config.load(self.cfg.get("project_root")))
        return self.config_view()

    def apply(self, cfg):
        with self.lock:
            self.cfg = cfg
            self.tailer.active_seconds = float(cfg["watch_active_hours"]) * 3600
            if self.notifier.send is notify.osascript:
                self.notifier.enabled = self.use_notify and bool(cfg.get("notify")) and sys.platform == "darwin"
            llm = cfg.get("llm") or {}
            if self.use_llm:
                self.chain = providers.Chain(llm) if llm.get("enabled") else None
            self.advisor.llm = llm
            self.advisor.chain = self.chain
            self.cat = catalog.load(cfg)

    def usage(self, days=7):
        days = max(1, min(int(days), 90))
        now = time.time()
        cached = self._usage_cache.get(days)
        if cached and now - cached[0] < 120:
            return cached[1]
        since = datetime.date.today() - datetime.timedelta(days=days)
        d = usage.data(since=since, cap=int(self.cfg["soft_limit"]))
        d["days"] = days
        self._usage_cache[days] = (now, d)
        return d

    def agents(self):
        return [install.status(a) for a in install.TARGETS]

    def health(self):
        return {"ok": True, "started": self.started, "port": self.port, "pid": os.getpid(), "sessions": len(self.tailer.sessions),
                "llm": self.chain.status() if self.chain else [], "llm_last_error": self.advisor.last_error,
                "advice": len(self.log.items)}


def once(cfg, as_json=False, out=None):
    """Scan, apply rules once and print sessions and the advice they produce (no LLM, no notifications)."""
    out = out or sys.stdout
    # A dry run keeps its advice in memory: it must not fill the real log or mark keys as sent.
    engine = Engine(cfg, use_llm=False, use_notify=False, out=io.StringIO(), log=store.AdviceLog(None))
    engine.tick()
    sessions = engine.sessions()
    advice = engine.log.list(1000)
    if as_json:
        json.dump({"sessions": sessions, "advice": advice, "quota": engine.quota()}, out, indent=2, default=str)
        out.write("\n")
        return 0
    print(f"{len(sessions)} active session(s)", file=out)
    for s in sessions:
        idle = s["idle_seconds"]
        idle = "-" if idle is None else f"{idle // 60}m"
        print(f"  {s['agent']:6} {round(s['context'] / 1000):>4}K  {(s['model'] or '?')[:18]:18} "
              f"{(s['effort'] or '-'):6} idle {idle:>6}  {(s['title'] or s['project'] or '?')[:50]}", file=out)
    print(f"{len(advice)} advice", file=out)
    for d in advice:
        sugg = d.get("suggested") or {}
        extra = " → " + " ".join(x for x in (sugg.get("model"), sugg.get("effort")) if x) if sugg else ""
        where = d.get("session_title") or d.get("project") or "-"
        print(f"  {d['severity'].upper():4} {d.get('agent') or '-':6} {where[:34]:34} "
              f"{d['title']}{extra}\n         {d['reason']}", file=out)
    return 0
