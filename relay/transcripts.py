"""Readers for agent session logs, normalised to one record per model call.

Claude Code: ~/.claude/projects/<project>/<session>.jsonl, assistant lines carry message.usage.
Codex:       ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl, event_msg/token_count lines carry
             info.last_token_usage. Neither format is a stable public interface, so every
             reader tolerates missing fields and returns None rather than raising.
"""
import glob
import json
import os
from dataclasses import dataclass

AGENTS = ("claude", "codex")
TAIL_SIZES = (1 << 20, 8 << 20, 64 << 20)


@dataclass
class Call:
    agent: str
    session: str
    project: str
    ts: str
    model: str
    context: int  # tokens sent as the prompt of this call (fresh + cache write + cache read)
    fresh: int
    cache_write: int
    cache_read: int
    output: int
    sidechain: bool = False


def _int(v):
    return v if isinstance(v, int) else 0


class ClaudeReader:
    agent = "claude"

    def __init__(self, path, seen=None):
        self.path = path
        folder = os.path.dirname(path)
        if "/subagents/" in path:
            folder = os.path.dirname(os.path.dirname(folder))
        home = os.path.expanduser("~").replace("/", "-") + "-"
        self.project = os.path.basename(folder).replace(home, "", 1)
        self.sidechain_file = "/subagents/" in path
        # Resumed and forked sessions copy earlier messages into new files; share `seen`
        # across files to count each model call once.
        self.seen = set() if seen is None else seen

    def feed(self, d):
        if d.get("type") != "assistant":
            return None
        m = d.get("message")
        if not isinstance(m, dict) or not isinstance(m.get("usage"), dict):
            return None
        key = (m.get("id"), d.get("requestId"))
        if key != (None, None):
            if key in self.seen:
                return None
            self.seen.add(key)
        u = m["usage"]
        fresh, cw, cr = _int(u.get("input_tokens")), _int(u.get("cache_creation_input_tokens")), \
            _int(u.get("cache_read_input_tokens"))
        return Call(self.agent, d.get("sessionId") or os.path.basename(self.path), self.project,
                    d.get("timestamp") or "", m.get("model") or "?", fresh + cw + cr, fresh, cw, cr,
                    _int(u.get("output_tokens")), bool(d.get("isSidechain")) or self.sidechain_file)


class CodexReader:
    agent = "codex"

    def __init__(self, path, seen=None):
        self.path = path
        self.session = os.path.basename(path)
        self.project = "?"
        self.model = "?"
        self.last_total = None
        self.rate_limits = None

    def feed(self, d):
        p = d.get("payload") if isinstance(d.get("payload"), dict) else {}
        kind = d.get("type")
        if kind == "session_meta":
            self.session = p.get("session_id") or p.get("id") or self.session
            self.project = p.get("cwd") or self.project
        elif kind == "turn_context":
            self.model = p.get("model") or self.model
        elif kind == "event_msg" and p.get("type") == "token_count":
            if isinstance(p.get("rate_limits"), dict):
                self.rate_limits = dict(p["rate_limits"], ts=d.get("timestamp"))
            info = p.get("info")
            if not isinstance(info, dict) or not isinstance(info.get("last_token_usage"), dict):
                return None
            total = (info.get("total_token_usage") or {}).get("total_tokens")
            if total is not None and total == self.last_total:
                return None  # the same usage re-emitted alongside a rate-limit update
            self.last_total = total
            u = info["last_token_usage"]
            inp, cached = _int(u.get("input_tokens")), _int(u.get("cached_input_tokens"))
            cw = _int(u.get("cache_write_input_tokens"))
            return Call(self.agent, self.session, self.project, d.get("timestamp") or "", self.model,
                        inp, max(inp - cached - cw, 0), cw, cached, _int(u.get("output_tokens")))
        return None


READERS = {"claude": ClaudeReader, "codex": CodexReader}


def _lines(path):
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.startswith("{"):
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def read_calls(agent, path, seen=None):
    reader = READERS[agent](path, seen)
    calls = [c for c in (reader.feed(d) for d in _lines(path)) if c]
    return calls, reader


def _tail_lines(path, size):
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        end = f.tell()
        start = max(end - size, 0)
        f.seek(start)
        chunk = f.read()
    lines = chunk.split(b"\n")
    if start > 0:
        lines = lines[1:]  # first line is cut in half
    return [ln for ln in lines if ln.startswith(b"{")], start == 0


def current_context(agent, path):
    """Prompt size of the latest main-thread call in a transcript, or None if unknown.

    Reads only the end of the file: transcripts reach hundreds of megabytes.
    """
    if not path or not os.path.isfile(path):
        return None
    for size in TAIL_SIZES:
        lines, whole = _tail_lines(path, size)
        reader = READERS[agent](path)
        last = None
        for raw in lines:  # forward order: the Codex reader needs session/model events first
            try:
                call = reader.feed(json.loads(raw))
            except ValueError:
                continue
            if call and not call.sidechain:
                last = call
        if last or whole:
            return last.context if last else None
    return None


def discover(agent, since_mtime=0.0):
    if agent == "claude":
        pattern = os.path.join(os.path.expanduser("~"), ".claude", "projects", "**", "*.jsonl")
    else:
        pattern = os.path.join(os.path.expanduser("~"), ".codex", "sessions", "**", "*.jsonl")
    for path in glob.glob(pattern, recursive=True):
        try:
            if os.path.getmtime(path) >= since_mtime:
                yield path
        except OSError:
            continue
