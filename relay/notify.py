"""macOS notifications for high-severity advice and ready next sessions, at most one per session and rule per hour."""
import subprocess
import sys
import time

SCRIPT = ["on run argv", "display notification (item 1 of argv) with title (item 2 of argv) "
          "subtitle (item 3 of argv)", "end run"]


def osascript(title, subtitle, message):
    args = ["osascript"]
    for line in SCRIPT:
        args += ["-e", line]
    try:  # text goes in as argv, never spliced into the script
        subprocess.run(args + [message[:240], title[:80], subtitle[:80]], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


class Notifier:
    def __init__(self, enabled=True, window=3600, send=osascript, clock=time.time):
        self.enabled = enabled and sys.platform == "darwin" if send is osascript else enabled
        self.window = window
        self.send = send
        self.clock = clock
        self.sent = {}

    def maybe(self, advice):
        nxt = advice.get("rule") == "next"
        if not self.enabled or not (nxt or advice.get("severity") == "high"):
            return False
        key = (advice.get("agent"), advice.get("session"), advice.get("rule"))
        now = self.clock()
        if now - self.sent.get(key, -1e18) < self.window:
            return False
        self.sent[key] = now
        where = advice.get("project") or advice.get("agent") or ""
        if nxt:
            sg = advice.get("suggested") or {}
            label = " ".join(p for p in (sg.get("model"), sg.get("effort")) if p)
            self.send("relay: next session ready", str(where)[-60:], f"{label} — {(sg.get('prompt') or '')[:80]}")
            return True
        self.send(f"relay: {advice.get('title', '')}", str(where)[-60:], advice.get("reason") or "")
        return True
