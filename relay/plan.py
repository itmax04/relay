"""The plan a run works through: `.relay/plan.md` in the target project.

    # <Plan title>

    Optional context, sent with every step (keep it short).

    ## 1. <Step title> [standard] [confirm] [agent: codex]
    Goal: what this step changes.
    Done when: what is true afterwards.
    Check: `python3 -m unittest discover -s tests`
    Files: relay/plan.py, tests/

Tags on the step heading: a tier (`light`, `standard`, `strong`; default `standard`), `confirm`
(the run pauses for the owner before this step) and `agent: claude|codex` (pin the agent).
`Check:` may repeat; each is a shell command run in the worktree, and the step passes only when
all exit 0. `Files:` limits which paths the step may change (a trailing `/` or a directory name
covers everything under it; shell globs work). Everything else under the heading goes to the agent.

Before the first step, three optional lines prepare the worktree (v1.1):
`Copy: .env, node_modules` (git-ignored paths copied from the main checkout), `Setup: <command>`
(repeatable, run once in the worktree) and `Baseline: <command>` (repeatable, must exit 0 before any
agent runs). They are not sent to the agent.
"""
import fnmatch
import re

TIERS = ("light", "standard", "strong")
AGENTS = ("codex", "claude")
_STEP = re.compile(r"^##\s+(?:(?:step\s+)?\d+[.):]?\s+)?(.*)$", re.I)
_TAG = re.compile(r"\[([^\]]+)\]")
_FIELD = re.compile(r"^(check|files)\s*:\s*(.*)$", re.I)
_PRE = re.compile(r"^(copy|setup|baseline)\s*:\s*(.*)$", re.I)


class PlanError(ValueError):
    pass


def _unquote(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] == "`":
        return s[1:-1].strip()
    return s


def parse(text):
    """-> {"title", "context", "steps": [{"n", "title", "tier", "confirm", "agent", "checks", "files", "body"}]}"""
    title, context, steps, cur = "", [], [], None
    pre = {"copy": [], "setup": [], "baseline": []}
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        if not in_code and line.startswith("# ") and not title and not steps and cur is None:
            title = line[2:].strip()
            continue
        m = None if in_code else _STEP.match(line)
        if m and line.startswith("## "):
            head = m.group(1)
            tags = [t.strip() for t in _TAG.findall(head)]
            cur = {"n": len(steps) + 1, "title": _TAG.sub("", head).strip(), "tier": "standard",
                   "confirm": False, "agent": None, "checks": [], "files": [], "body": []}
            for t in tags:
                low = t.lower()
                if low in TIERS:
                    cur["tier"] = low
                elif low == "confirm":
                    cur["confirm"] = True
                elif low in AGENTS:
                    cur["agent"] = low
                elif low.startswith("agent:") and low[6:].strip() in AGENTS:
                    cur["agent"] = low[6:].strip()
                else:
                    raise PlanError(f"step {cur['n']}: unknown tag [{t}]")
            steps.append(cur)
            continue
        if cur is None:
            f = None if in_code else _PRE.match(line.strip())
            if f:
                key, val = f.group(1).lower(), f.group(2)
                if key == "copy":
                    pre["copy"] += [_unquote(x) for x in val.split(",") if _unquote(x)]
                elif _unquote(val):
                    pre[key].append(_unquote(val))
                continue
            context.append(line)
            continue
        f = None if in_code else _FIELD.match(line.strip())
        if f and f.group(1).lower() == "check":
            if _unquote(f.group(2)):
                cur["checks"].append(_unquote(f.group(2)))
            continue
        if f:
            cur["files"] += [_unquote(p) for p in f.group(2).split(",") if _unquote(p)]
            continue
        cur["body"].append(line)
    for s in steps:
        s["body"] = "\n".join(s["body"]).strip()
        if not s["title"]:
            raise PlanError(f"step {s['n']}: no title")
    if not steps:
        raise PlanError("no steps: each step starts with a '## ' heading")
    return {"title": title or "Untitled plan", "context": "\n".join(context).strip(), "steps": steps,
            "copy": pre["copy"], "setup": pre["setup"], "baseline": pre["baseline"]}


def allowed(path, patterns):
    """Whether a repo-relative path is covered by a step's `Files:` list (no list = anything)."""
    if not patterns:
        return True
    for p in patterns:
        p = p[2:] if p.startswith("./") else p
        base = p.rstrip("/")
        if path == base or path.startswith(base + "/") or fnmatch.fnmatch(path, p):
            return True
    return False


DRAFT_PROMPT = """You are planning work for an automated dispatcher. It runs each step of your plan in a
fresh coding-agent process that sees only that step's text and the previous step's short summary,
so every step must stand on its own. Read the repository as needed, but do not change any file.

Task from the owner:
{task}

Reply with the plan only, as Markdown in exactly this format (no text before or after it):

# <short plan title>

<1-5 lines of context every step needs: where the code lives, conventions, constraints>
Copy: <git-ignored files or directories the checks need, from the main checkout, e.g. .env, node_modules; omit if none>
Setup: `<command run once in the fresh worktree, e.g. npm ci; omit if none>`
Baseline: `<command that must already exit 0 before any change, e.g. the build or the test suite>`

## 1. <step title> [light|standard|strong]
Goal: <what this step changes>
Done when: <observable result>
Check: `<shell command that exits 0 only when the step is done>`
Files: <comma-separated paths or directories this step may change>

Rules:
- Small steps: each one a single focused change that one agent run can finish.
- Tier: light for mechanical edits, standard for normal coding, strong only for hard design or debugging.
- Every step needs at least one Check command that runs locally without network access and
  every step must change at least one file.
- Add [confirm] to a step that deletes data, changes public interfaces or needs the owner's eyes first.
- Copy/Setup/Baseline lines go before the first step; Setup may only create git-ignored files.
- At most {max_steps} steps.
"""
