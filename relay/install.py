"""`relay install|uninstall <agent>`: wire the guard, the skills and the stage rule into an agent.

Skills: every `skills/<name>/` with a SKILL.md (relay's own and the vendored third-party pack, see
skills/THIRD_PARTY.md) is symlinked into the agent's skills directory. Only relay's links are
replaced or removed; a file, directory or foreign link with the same name is skipped and reported.

Edits are merged into the agent's existing user-level config, a timestamped backup is written
first, and running install twice leaves one copy of every entry. The stage rule is a block
between marker lines in the agent's global instructions file; text outside it is left alone.
"""
import json
import os
import re
import shutil
import sys
import time

from relay import catalog, config

REPO = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
EVENTS = ("SessionStart", "UserPromptSubmit", "Stop")
MARK = "/bin/relay\" hook "

RULE_BEGIN = "<!-- relay:plan-rule begin -->"
RULE_END = "<!-- relay:plan-rule end -->"
_BLOCK_RE = re.compile(re.escape(RULE_BEGIN) + r".*?" + re.escape(RULE_END), re.S)
# Sent with every call, so it stays short; examples and details are in the relay-plan skill.
RULE = """## Stages and model tiers (managed by `relay install`, removed by `relay uninstall`)
For tasks with several stages (not questions or small edits):
- Start with a short plan of stages; give each a tier: strong (design, hard debugging), standard (implementation from a plan), light (mechanical edits, tests, docs).
- Stop only at a stage boundary, when the next stage has a different tier or the context is above {limit}K.
- Before stopping: verify the result, update the handoff (relay-handoff skill).
- Whenever a session ends and the work goes on in a new one (stage boundary, context limit, finished step with a next one), end with one line: the next stage, its tier → model and effort from the Tiers line, "in a new session", and a ready prompt naming the concrete next action. E.g.: Next stage — delivery, standard → <model> medium, in a new session. Prompt: «Deliver PR #83 from the handoff, then prepare the handoff for T-6a2b». Then, as the very last line, exactly: <!-- relay-next {{"model": "<model>", "effort": "<effort>", "prompt": "<same prompt>"}} -->
- Never switch the model mid-session: continue in a new session from the handoff.
Tiers: {tiers}. Output format and examples: relay-plan skill."""


def _cmd(*args):
    return f"\"{sys.executable}\" \"{os.path.join(REPO, 'bin', 'relay')}\" " + " ".join(args)


TARGETS = {
    "claude": {
        "config": lambda: config.home(".claude", "settings.json"),
        "skills": lambda: config.home(".claude", "skills"),
        "instructions": lambda: config.home(".claude", "CLAUDE.md"),
    },
    "codex": {
        "config": lambda: config.home(".codex", "hooks.json"),
        "skills": lambda: config.home(".agents", "skills"),
        "instructions": lambda: config.home(".codex", "AGENTS.md"),
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
    _write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _write_text(path, text):
    if os.path.exists(path):
        shutil.copy2(path, f"{path}.relay-backup-{time.strftime('%Y%m%d-%H%M%S')}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".relay-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def rule_block(agent):
    """The block for `agent`: models from catalog.STAGE_TIERS, the limit from the user's soft_limit."""
    limit = config.load_user().get("soft_limit") or config.DEFAULTS["soft_limit"]
    rule = RULE.format(limit=round(limit / 1000), tiers=catalog.stage_tiers(agent))
    return f"{RULE_BEGIN}\n{rule}\n{RULE_END}"


def _put_rule(agent, path):
    """Replace the stage-rule block in place, or append it; write only when the file changes."""
    old = _read_text(path)
    if _BLOCK_RE.search(old):
        new = _BLOCK_RE.sub(lambda _: rule_block(agent), old, count=1)
    elif RULE_BEGIN in old or RULE_END in old:
        print(f"skip stage rule: {path} has a broken relay block, fix or remove its marker lines")
        return
    else:
        new = (old.rstrip("\n") + "\n\n" if old.strip() else "") + rule_block(agent) + "\n"
    if new != old:
        _write_text(path, new)
    print(f"stage rule installed: {path}")


def _drop_rule(path):
    old = _read_text(path)
    if not _BLOCK_RE.search(old):
        return
    rest = re.sub(r"\n*" + _BLOCK_RE.pattern + r"\n?", "\n", old, flags=re.S).strip("\n")
    _write_text(path, rest + "\n" if rest else "")
    print(f"stage rule removed: {path}")


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


def bundled_skills():
    """Names of the skill directories this checkout ships: skills/<name>/SKILL.md."""
    root = os.path.join(REPO, "skills")
    return sorted(n for n in os.listdir(root) if os.path.isfile(os.path.join(root, n, "SKILL.md")))


def _is_relay_link(link):
    """A symlink to skills/<its name> in a relay checkout (this one, the main repo or a worktree)."""
    if not os.path.islink(link):
        return False
    target = os.path.realpath(link)
    checkout = os.path.dirname(os.path.dirname(target))
    return (os.path.basename(target) == os.path.basename(link)
            and os.path.basename(os.path.dirname(target)) == "skills"
            and os.path.isfile(os.path.join(checkout, "relay", "install.py")))


def _link_skill(name, link):
    if _is_relay_link(link):
        os.remove(link)
    elif os.path.lexists(link):
        print(f"skip skill: {link} exists and is not a relay link")
        return
    os.makedirs(os.path.dirname(link), exist_ok=True)
    os.symlink(os.path.join(REPO, "skills", name), link)
    print(f"skill linked: {link}")


def _unlink_skills(folder):
    """Remove every relay link in `folder`, also links to skills this checkout no longer ships."""
    try:
        names = sorted(os.listdir(folder))
    except FileNotFoundError:
        return
    for name in names:
        link = os.path.join(folder, name)
        if _is_relay_link(link):
            os.remove(link)
            print(f"skill unlinked: {link}")


def linked_skills(agent):
    """Bundled skills that have a relay link in the agent's skills directory."""
    folder = TARGETS[agent]["skills"]()
    return [n for n in bundled_skills() if _is_relay_link(os.path.join(folder, n))]


def install(agent, autocompact=None, statusline=True, rule=True):
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
    for name in bundled_skills():
        _link_skill(name, os.path.join(t["skills"](), name))
    if rule:
        _put_rule(agent, t["instructions"]())
    if agent == "codex":
        print("Codex runs new hooks only after you trust them: open Codex and review them with /hooks.")


def status(agent):
    """Whether relay's hooks, status line, skills and stage rule are present for the agent."""
    t = TARGETS[agent]
    path = t["config"]()
    try:
        data = _read(path)
    except ValueError:
        data = {}
    linked = linked_skills(agent)
    events = sorted(e for e, groups in (data.get("hooks") or {}).items()
                    if any(MARK in str(h.get("command", "")) for g in groups or [] for h in g.get("hooks", [])))
    out = {"agent": agent, "config": path, "config_exists": os.path.exists(path), "hooks": events,
           "hooks_installed": set(EVENTS) <= set(events),
           "skill_linked": "relay-handoff" in linked,
           "plan_skill_linked": "relay-plan" in linked,
           "skills_linked": linked,
           "skills_missing": [n for n in bundled_skills() if n not in linked],
           "rule_installed": rule_block(agent) in _read_text(t["instructions"]())}  # present and current
    if agent == "claude":
        out["statusline"] = "/bin/relay\" statusline" in str((data.get("statusLine") or {}).get("command", ""))
        out["autocompact"] = data.get("autoCompactWindow")
    return out


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
    _unlink_skills(t["skills"]())
    _drop_rule(t["instructions"]())
