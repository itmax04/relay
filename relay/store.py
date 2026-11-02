"""Advice log: <state>/advice.jsonl, append-only.

Advice lines carry {"id", "ts", "key", ...}; the app's verdicts are appended as
{"type": "outcome", "id", "outcome", "note", "ts"} and folded into the advice on load.
"""
import json
import os
import threading
import time
import uuid

OUTCOMES = ("useful", "wrong", None)


class AdviceLog:
    def __init__(self, path, keep=1000):
        self.path = path
        self.keep = keep
        self.lock = threading.Lock()
        self.items = []
        self.by_id = {}
        self.keys = set()
        self._load()

    def _load(self):
        if not self.path:  # in-memory log, for dry runs
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return
        for line in lines:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") == "outcome":
                if d.get("id") in self.by_id:
                    self.by_id[d["id"]].update(outcome=d.get("outcome"), outcome_note=d.get("note"))
            elif d.get("id"):
                self._remember(d)

    def _remember(self, d):
        self.items.append(d)
        self.by_id[d["id"]] = d
        if d.get("key"):
            self.keys.add(d["key"])
        if len(self.items) > self.keep:
            old = self.items.pop(0)
            self.by_id.pop(old["id"], None)

    def _append(self, d):
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

    def add(self, advice):
        """Store new advice; returns it with id/ts, or None if its key was already emitted."""
        with self.lock:
            if advice.get("key") in self.keys:
                return None
            d = dict(advice, id=uuid.uuid4().hex[:12], ts=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
            d.setdefault("source", "rules")
            d.setdefault("outcome", None)
            self._remember(d)
            self._append(d)
            return d

    def seen(self, key):
        with self.lock:
            return key in self.keys

    def set_outcome(self, advice_id, outcome, note=None):
        if outcome not in OUTCOMES:
            raise ValueError(f"outcome must be one of {OUTCOMES}")
        with self.lock:
            d = self.by_id.get(advice_id)
            if d is None:
                return None
            d.update(outcome=outcome, outcome_note=note)
            self._append({"type": "outcome", "id": advice_id, "outcome": outcome, "note": note,
                          "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
            return dict(d)

    def list(self, limit=100, session=None):
        with self.lock:
            items = [d for d in self.items if session is None or d.get("session") == session]
            return [dict(d) for d in items[-limit:]][::-1]
