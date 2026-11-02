"""Live per-session state built by tailing agent transcripts as they grow.

Each transcript is read from a remembered byte offset; a newly found file is bootstrapped from its
last few megabytes only. Model calls come from the readers in transcripts.py; this module adds
what the rules need: prompts, tool calls, failures, large tool results, effort and idle time.
"""
import collections
import datetime
import hashlib
import json
import os
import re
import time

from relay import transcripts

BOOTSTRAP_BYTES = 4 << 20
CHARS_PER_TOKEN = 4  # rough; only used to size tool results, which carry no token counts
MECHANICAL_TOOLS = {"Read", "Edit", "MultiEdit", "Write", "Glob", "Grep", "LS", "TodoWrite", "NotebookEdit",
                    "apply_patch"}
MECHANICAL_BASH = re.compile(r"^\s*(ls|cat|head|tail|sed -n|grep|rg|find|wc|pwd|echo|mkdir|cp|mv|touch|"
                             r"git (status|diff|log|add|show))\b")
_EXIT = re.compile(r"(?i)(exit code|exit_code|exited with code)\D{0,4}([1-9]\d*)")
_FAILED = re.compile(r"(?i)^\s*(error\b|script failed|traceback|fatal:)")


def parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _text(content):
    """Plain text of a message/tool-result content (string or list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                parts.append(b.get("text") if isinstance(b.get("text"), str) else
                             _text(b.get("content")) if b.get("type") == "tool_result" else "")
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(p for p in parts if p)
    return ""


def _signature(name, args):
    if name == "Bash":
        key = " ".join(str(args.get("command") or "").split())[:300]
    elif isinstance(args, dict) and args.get("file_path"):
        key = args["file_path"]
    else:
        key = hashlib.sha1(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()[:12]
    return f"{name}:{key}"


def _label(name, args):
    if not isinstance(args, dict):
        return name
    for k in ("command", "file_path", "pattern", "cmd", "code"):
        if args.get(k):
            return f"{name}: {' '.join(str(args[k]).split())[:160]}"
    return name


class Session:
    def __init__(self, agent, path):
        self.agent = agent
        self.path = path
        self.id = os.path.splitext(os.path.basename(path))[0]
        self.project = "?"
        self.cwd = None
        self.title = None
        self.entrypoint = None
        self.model = None
        self.effort = None
        self.context = 0
        self.context_window = None
        self.peak_context = 0
        self.calls = 0  # model calls seen since relay started watching (bootstrap included)
        self.turns = 0  # user prompts seen
        self.tool_calls = 0
        self.last_call_ts = None
        self.last_activity = None
        self.last_prompt = ""
        self.rate_limits = None
        self.recent_calls = collections.deque(maxlen=30)  # {"id", "tools", "output", "mechanical"}
        self.recent_tools = collections.deque(maxlen=40)  # {"sig", "label", "error", "excerpt", "ts"}
        self.large_results = collections.deque(maxlen=10)
        self.files = collections.deque(maxlen=20)
        self._pending = {}  # tool_use_id -> (name, args)
        self._codex_tools = []  # tool calls since the last Codex token_count event
        self.reader = transcripts.READERS[agent](path)
        self.offset = 0

    # --- feeding -------------------------------------------------------------------------------
    def feed(self, d):
        ts = parse_ts(d.get("timestamp"))
        if ts:
            self.last_activity = max(self.last_activity or 0, ts)
        call = self.reader.feed(d)
        if call and not call.sidechain:
            self.context = call.context
            self.peak_context = max(self.peak_context, call.context)
            self.model = call.model if call.model != "?" else self.model
            self.calls += 1
            self.last_call_ts = parse_ts(call.ts) or self.last_call_ts
        if self.agent == "claude":
            self._feed_claude(d, call)
        else:
            self._feed_codex(d, call)

    def _feed_claude(self, d, call):
        kind = d.get("type")
        if kind == "custom-title" and d.get("customTitle"):
            self.title = d["customTitle"]
        if d.get("isSidechain") or kind not in ("user", "assistant"):
            return
        self.cwd = d.get("cwd") or self.cwd
        self.project = self.reader.project
        self.entrypoint = d.get("entrypoint") or self.entrypoint
        m = d.get("message") if isinstance(d.get("message"), dict) else {}
        content = m.get("content")
        if kind == "assistant":
            self.effort = d.get("effort") or self.effort
            self._note_call(m.get("id"), content, (m.get("usage") or {}).get("output_tokens") or 0)
            return
        if d.get("toolUseResult") is not None or (isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content)):
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    self._note_result(b.get("tool_use_id"), _text(b.get("content")), bool(b.get("is_error")),
                                      d.get("timestamp"))
            return
        text = _text(content)
        if not d.get("isMeta") and text and not text.lstrip().startswith("<"):
            self.turns += 1
            self.last_prompt = text[:2000]

    def _note_call(self, msg_id, content, output):
        tools, args_list = [], []
        for b in content if isinstance(content, list) else []:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                name, args = b.get("name") or "?", b.get("input") if isinstance(b.get("input"), dict) else {}
                tools.append(name)
                args_list.append((name, args))
                self._pending[b.get("id")] = (name, args)
                self.tool_calls += 1
                if args.get("file_path"):
                    self.files.append(args["file_path"])
        entry = self.recent_calls[-1] if self.recent_calls and self.recent_calls[-1]["id"] == msg_id else None
        if entry is None:
            entry = {"id": msg_id, "tools": [], "output": 0, "args": []}
            self.recent_calls.append(entry)
        entry["tools"] += tools
        entry["args"] += args_list
        entry["output"] = max(entry["output"], output)
        entry["mechanical"] = bool(entry["tools"]) and all(
            t in MECHANICAL_TOOLS or (t == "Bash" and MECHANICAL_BASH.match(str(a.get("command") or "")))
            for t, a in entry["args"])
        if len(self._pending) > 200:
            self._pending.pop(next(iter(self._pending)))

    def _note_result(self, tool_id, text, is_error, ts):
        name, args = self._pending.pop(tool_id, ("?", {}))
        tokens = len(text) // CHARS_PER_TOKEN
        self.recent_tools.append({"sig": _signature(name, args), "label": _label(name, args), "error": is_error,
                                  "excerpt": text[:400] if is_error else "", "ts": ts})
        if tokens >= 5000:
            self.large_results.append({"label": _label(name, args), "tokens": tokens, "ts": ts,
                                       "narrow": any(k in args for k in ("offset", "limit", "pages"))})

    def _feed_codex(self, d, call):
        kind = d.get("type")
        p = d.get("payload") if isinstance(d.get("payload"), dict) else {}
        if kind == "session_meta":
            self.cwd = p.get("cwd") or self.cwd
            self.project = self.cwd or self.project
            self.entrypoint = p.get("originator") or self.entrypoint
            self.context_window = p.get("context_window") or self.context_window
        elif kind == "turn_context":
            self.effort = p.get("effort") or self.effort
            self.cwd = p.get("cwd") or self.cwd
        elif kind == "event_msg" and p.get("type") == "task_started":
            self.context_window = p.get("model_context_window") or self.context_window
        if self.reader.rate_limits:
            self.rate_limits = self.reader.rate_limits
        if call:
            self._note_call_codex(d.get("timestamp"), call.output)
        if kind != "response_item":
            return
        t = p.get("type")
        if t in ("function_call", "custom_tool_call"):
            raw = p.get("arguments") if t == "function_call" else p.get("input")
            args = raw
            if isinstance(raw, str):
                try:
                    args = json.loads(raw)
                except ValueError:
                    args = {"code": raw}
            args = args if isinstance(args, dict) else {"code": str(raw)}
            name = p.get("name") or "?"
            self._pending[p.get("call_id")] = (name, args)
            self.tool_calls += 1
            self._codex_tools.append((name, args))
        elif t in ("function_call_output", "custom_tool_call_output"):
            out = p.get("output")
            text = out if isinstance(out, str) else _text(out) or json.dumps(out, default=str)
            error = bool(_EXIT.search(text[:2000]) or _FAILED.match(text))
            self._note_result(p.get("call_id"), text, error, d.get("timestamp"))
        elif t == "message" and p.get("role") == "user":
            text = _text(p.get("content"))
            if text and not text.lstrip().startswith("<"):
                self.turns += 1
                self.last_prompt = text[:2000]

    def _note_call_codex(self, ts, output):
        # A token_count event closes one model call; the tool calls since the previous one belong to it.
        tools = self._codex_tools
        names = [n for n, _ in tools]
        self.recent_calls.append({"id": ts, "tools": names, "output": output, "args": list(tools),
                                  "mechanical": bool(names) and all(n == "apply_patch" for n in names)})
        self._codex_tools = []

    # --- views ---------------------------------------------------------------------------------
    def idle(self, now=None):
        last = self.last_call_ts or self.last_activity
        return None if last is None else max((now or time.time()) - last, 0)

    def snapshot(self, now=None):
        rl = self.rate_limits or {}
        return {"id": self.id, "agent": self.agent, "path": self.path, "project": self.project, "cwd": self.cwd,
                "title": self.title, "entrypoint": self.entrypoint, "model": self.model, "effort": self.effort,
                "context": self.context, "peak_context": self.peak_context, "context_window": self.context_window,
                "calls": self.calls, "turns": self.turns, "tool_calls": self.tool_calls,
                "failures": sum(1 for t in self.recent_tools if t["error"]),
                "idle_seconds": None if self.idle(now) is None else round(self.idle(now)),
                "last_activity": self.last_activity, "last_prompt": self.last_prompt[:300],
                "rate_limits": {k: rl.get(k) for k in ("primary", "secondary") if rl.get(k)} or None}


class Tailer:
    """Finds active transcripts and feeds their new lines into Session objects."""

    def __init__(self, active_hours=12.0, agents=transcripts.AGENTS, discover=transcripts.discover):
        self.active_seconds = active_hours * 3600
        self.agents = agents
        self.discover = discover
        self.sessions = {}  # path -> Session

    def scan(self, now=None):
        """Pick up new transcripts and drop ones that went quiet. Returns newly added sessions."""
        now = now or time.time()
        added = []
        for agent in self.agents:
            for path in self.discover(agent, now - self.active_seconds):
                if path in self.sessions or (agent == "claude" and "/subagents/" in path):
                    continue
                s = Session(agent, path)
                self._bootstrap(s)
                self.sessions[path] = s
                added.append(s)
        for path, s in list(self.sessions.items()):
            try:
                if os.path.getmtime(path) < now - self.active_seconds:
                    del self.sessions[path]
            except OSError:
                del self.sessions[path]
        return added

    def _bootstrap(self, s):
        try:
            size = os.path.getsize(s.path)
        except OSError:
            return
        start = max(size - BOOTSTRAP_BYTES, 0)
        if s.agent == "codex" and start > 0:
            self._read(s, 0, min(size, 256 << 10), only=("session_meta",))  # it sits at the top
        s.offset = start
        self._read(s, start, size, skip_first=start > 0)

    def poll(self):
        """Read appended lines from every tracked transcript. Returns sessions that changed."""
        changed = []
        for s in list(self.sessions.values()):
            try:
                size = os.path.getsize(s.path)
            except OSError:
                continue
            if size < s.offset:  # truncated or replaced
                s.offset = 0
            if size > s.offset and self._read(s, s.offset, size):
                changed.append(s)
        return changed

    def _read(self, s, start, end, skip_first=False, only=None):
        with open(s.path, "rb") as f:
            f.seek(start)
            chunk = f.read(end - start)
        lines = chunk.split(b"\n")
        tail = lines.pop()  # incomplete last line (or b"" after a trailing newline)
        if skip_first and lines:
            lines = lines[1:]
        fed = 0
        for raw in lines:
            if raw.startswith(b"{"):
                try:
                    d = json.loads(raw)
                    if only is None or d.get("type") in only:
                        s.feed(d)
                        fed += 1
                except ValueError:
                    continue
        if only is None:
            s.offset = end - len(tail)
        return fed
