"""`relay install|uninstall <agent>`: wire the guard and the handoff skill into an agent.

Edits are merged into the agent's existing user-level config, a timestamped backup is written
first, and running install twice leaves one copy of every entry.
"""
import json
import os
import shutil
import sys
import time

from relay import config

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
SKILL_SRC = os.path.join(REPO, "skills", "relay-handoff")
EVENTS = ("SessionStart", "UserPromptSubmit", "Stop")
MARK = "/bin/relay\" hook "


def _cmd(*args):
    return f"\"{sys.executable}\" \"{os.path.join(REPO, 'bin', 'relay')}\" " + " ".join(args)


TARGETS = {
    "claude": {
        "config": lambda: config.home(".claude", "settings.json"),
        "skill_link": lambda: config.home(".claude", "skills", "relay-handoff"),
    },
    "codex": {
        "config": lambda: config.home(".codex", "hooks.json"),
        "skill_link": lambda: config.home(".agents", "skills", "relay-handoff"),
    },
}


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}


def _write(path, data):
    if os.path.exists(path):
        shutil.copy2(path, f"{path}.relay-backup-{time.strftime('%Y%m%d-%H%M%S')}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".relay-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _strip_ours(hooks):
    """Drop relay handlers from a hooks mapping; drop groups and events left empty."""
    for event in list(hooks):
        groups = []
        for group in hooks[event] or []:
            handlers = [h for h in group.get("hooks", []) if MARK not in str(h.get("command", ""))]
            if handlers:
                groups.append(dict(group, hooks=handlers))
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]
    return hooks


def _link_skill(link):
    if os.path.islink(link):
        os.remove(link)
    elif os.path.exists(link):
        print(f"skip skill: {link} exists and is not a relay link")
        return
    os.makedirs(os.path.dirname(link), exist_ok=True)
    os.symlink(SKILL_SRC, link)
    print(f"skill linked: {link}")


def _unlink_skill(link):
    if os.path.islink(link) and os.path.realpath(link) == os.path.realpath(SKILL_SRC):
        os.remove(link)
        print(f"skill unlinked: {link}")


def install(agent, autocompact=None, statusline=True):
    t = TARGETS[agent]
    path = t["config"]()
    data = _read(path)
    hooks = _strip_ours(data.get("hooks") or {})
    for event in EVENTS:
        hooks.setdefault(event, []).append(
            {"hooks": [{"type": "command", "command": _cmd("hook", agent), "timeout": 10}]})
    data["hooks"] = hooks
    if agent == "claude":
        current = (data.get("statusLine") or {}).get("command", "")
        if statusline and (not current or "/bin/relay\" statusline" in current):
            data["statusLine"] = {"type": "command", "command": _cmd("statusline")}
        elif statusline:
            print(f"skip status line: another one is configured ({current})")
        if autocompact:
            data["autoCompactWindow"] = int(autocompact)
    _write(path, data)
    print(f"hooks installed: {path} ({', '.join(EVENTS)})")
    _link_skill(t["skill_link"]())
    if agent == "codex":
        print("Codex runs new hooks only after you trust them: open Codex and review them with /hooks.")


def uninstall(agent, drop_autocompact=False):
    t = TARGETS[agent]
    path = t["config"]()
    data = _read(path)
    if data:
        hooks = _strip_ours(data.get("hooks") or {})
        if hooks:
            data["hooks"] = hooks
        else:
            data.pop("hooks", None)
        if agent == "claude":
            if "/bin/relay\" statusline" in (data.get("statusLine") or {}).get("command", ""):
                data.pop("statusLine")
            if drop_autocompact:
                data.pop("autoCompactWindow", None)
        _write(path, data)
        print(f"hooks removed: {path}")
    _unlink_skill(t["skill_link"]())
