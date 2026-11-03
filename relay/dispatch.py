"""`relay run`: work through `.relay/plan.md` one step at a time, each step in a fresh agent process.

Design: docs/design/dispatcher.md. In short: a git worktree on branch `relay/run-<id>`, one commit
per passed step, never a push. Each step runs `codex exec --json` with the tier's model and effort;
the dispatcher then runs the step's checks, requires a non-empty diff inside the allowed files,
retries with the error output (max_retries), raises the tier, and finally stops and notifies.

State lives in <state_dir>/runs/<id>/: run.json (the snapshot `relay watch` serves), plan.md,
per-attempt logs. The owner controls a running run through files there, written by
`relay run stop|approve` or the app: `stop`, and `approve-<gate>` for the plan and [confirm] steps.
"""
import datetime
import glob
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time

from relay import config, notify, plan as planlib

TIERS = planlib.TIERS
TAIL = 4000          # characters of failure output passed to the retry
POLL = 1.0
FINAL = ("done", "failed", "stopped")


def runs_dir():
    return os.path.join(config.state_dir(), "runs")


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _tail(text, n=TAIL):
    text = text or ""
    return text if len(text) <= n else "…" + text[-n:]


def _last_line(text):
    lines = [l for l in (text or "").strip().splitlines() if l.strip()]
    return lines[-1][:200] if lines else ""


def _git(cwd, *args, check=True):
    p = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True)
    if check and p.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip() or p.stdout.strip()}")
    return p.stdout.strip()


# Bytecode caches written by the agent's or the checks' Python runs are not part of a step.
def _env():
    return dict(os.environ, PYTHONDONTWRITEBYTECODE="1")


def _dirty(wt):
    """{path: porcelain status} of every changed or untracked file in the worktree."""
    out = {}
    raw = subprocess.run(["git", "-C", wt, "status", "--porcelain", "-uall", "-z"], capture_output=True,
                         text=True, check=True).stdout  # not _git(): strip() would eat the status column
    for line in raw.split("\0"):
        if len(line) > 3:
            out[line[3:]] = line[:2]
    return out


def _undo_check_output(wt, agent_paths):
    """Drop what only the checks changed (build output, caches): it is not the agent's change."""
    for path, status in _dirty(wt).items():
        if agent_paths.get(path) == status:
            continue
        if status == "??" and path not in agent_paths:
            try:
                os.remove(os.path.join(wt, path))
            except OSError:
                pass
        elif path not in agent_paths:
            _git(wt, "checkout", "--", path, check=False)


# --- state ----------------------------------------------------------------------------------------
def load(run_id):
    try:
        with open(os.path.join(runs_dir(), run_id, "run.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def list_runs(limit=50):
    out = []
    for path in glob.glob(os.path.join(runs_dir(), "*", "run.json")):
        try:
            with open(path, encoding="utf-8") as f:
                out.append(json.load(f))
        except (OSError, ValueError):
            continue
    for r in out:
        if r.get("status") not in FINAL and not _alive(r.get("pid")):
            r["status"], r["error"] = "stopped", r.get("error") or "dispatcher process is gone"
    return sorted(out, key=lambda r: r.get("created") or "", reverse=True)[:limit]


def _alive(pid):
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def request_stop(run_id):
    d = os.path.join(runs_dir(), run_id)
    if not os.path.isfile(os.path.join(d, "run.json")):
        return False
    open(os.path.join(d, "stop"), "w").close()
    return True


def approve(run_id, gate=None):
    """Approve the gate the run is waiting at (or a named one). Returns the gate or None."""
    r = load(run_id)
    if not r:
        return None
    gate = gate or (r.get("gate") or {}).get("id")
    if not gate:
        return None
    open(os.path.join(runs_dir(), run_id, f"approve-{gate}"), "w").close()
    return gate


# --- Codex --------------------------------------------------------------------------------------
FATAL = re.compile(r"model_not_found|not supported when using|requires a newer version|"
                   r"unauthori[sz]ed|not logged in|login required", re.I)
QUOTA = re.compile(r"usage limit|rate limit|too many requests|\b429\b", re.I)


def codex_command(cfg_run, model, effort, cwd, last_path, prompt):
    cmd = [cfg_run.get("codex_bin") or "codex", "exec", "--json", "-m", model, "-s", "workspace-write",
           "-C", cwd, "-o", last_path]
    if effort:
        cmd += ["-c", f"model_reasoning_effort={effort}"]
    return cmd + [prompt]


def read_codex_events(path):
    """thread id, usage, failure message and file changes from `codex exec --json` output."""
    out = {"thread_id": None, "usage": None, "error": None, "completed": False, "messages": []}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return out
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        t = d.get("type")
        if t == "thread.started":
            out["thread_id"] = d.get("thread_id")
        elif t == "turn.completed":
            out["completed"], out["usage"] = True, d.get("usage")
        elif t == "turn.failed":
            out["error"] = _error_text((d.get("error") or {}).get("message"))
        elif t == "error" and not out["error"]:
            out["error"] = _error_text(d.get("message"))
        elif t == "item.completed" and (d.get("item") or {}).get("type") == "agent_message":
            out["messages"].append(d["item"].get("text") or "")
    return out


def _error_text(msg):
    """Codex wraps the API error JSON in a string; keep the human part."""
    try:
        inner = json.loads(msg)
        return ((inner.get("error") or {}).get("message") or msg) + \
            (f" ({inner['error']['code']})" if (inner.get("error") or {}).get("code") else "")
    except (TypeError, ValueError, AttributeError):
        return msg


def codex_rate_limits(thread_id, sessions_root=None):
    """The last `rate_limits` in the rollout of a thread (exec --json output carries none)."""
    if not thread_id:
        return None
    root = sessions_root or config.home(".codex", "sessions")
    paths = glob.glob(os.path.join(root, "*", "*", "*", f"rollout-*{thread_id}.jsonl"))
    last = None
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if '"rate_limits"' not in line:
                        continue
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    rl = (d.get("payload") or {}).get("rate_limits") if isinstance(d.get("payload"), dict) else None
                    if isinstance(rl, dict):
                        last = dict(rl, ts=d.get("timestamp"))
        except OSError:
            continue
    return last


def quota_wait(rl, cfg_run, now=None):
    """Epoch seconds to wait until, or None when the reading allows another step."""
    if not isinstance(rl, dict):
        return None
    now = now or time.time()
    for window, limit in (("primary", cfg_run.get("quota_switch_at", 80)),
                          ("secondary", cfg_run.get("quota_week_stop", 98))):
        w = rl.get(window) or {}
        used, resets = w.get("used_percent"), w.get("resets_at")
        if isinstance(used, (int, float)) and isinstance(resets, (int, float)) and resets > now and used >= limit:
            return resets
    return None


# --- prompt ---------------------------------------------------------------------------------------
def step_prompt(plan, step, prev_summary, failure, total):
    parts = [f"You are doing step {step['n']} of {total} of a plan in this git repository. "
             "Do only this step; later steps are done by other agents.",
             f"Plan: {plan['title']}"]
    if plan.get("context"):
        parts.append("Plan context:\n" + plan["context"][:2000])
    parts.append(f"Step {step['n']}: {step['title']}\n{step['body']}".rstrip())
    if step["files"]:
        parts.append("You may change only these paths: " + ", ".join(step["files"]))
    if step["checks"]:
        parts.append("After you finish, the dispatcher runs these checks in this directory; the step "
                     "passes only if all of them exit 0:\n" + "\n".join(f"- {c}" for c in step["checks"]))
    if prev_summary:
        parts.append("Summary from the previous step:\n" + prev_summary)
    if failure:
        parts.append("Your previous attempt at this step failed. The changes it made are still in the "
                     "working tree. Fix the problem:\n" + failure)
    parts.append("Do not commit, push or create branches: the dispatcher commits after the checks pass. "
                 "End your final message with a line 'Step summary:' followed by 1-3 sentences the next "
                 "step needs (what changed, where, anything left open).")
    return "\n\n".join(parts)


def summary_from(text):
    m = re.search(r"step summary\s*:?\**\s*(.*)$", text or "", re.I | re.S)
    return (m.group(1).strip() if m else "")[:1500]


# --- the run --------------------------------------------------------------------------------------
class Stopped(Exception):
    pass


class Run:
    def __init__(self, cfg, plan_path, approved=False, out=None, notifier=None, run_id=None):
        self.cfg = cfg
        self.rc = dict(config.DEFAULTS["run"], **(cfg.get("run") or {}))
        self.out = out or sys.stdout
        self.notify = notifier or (lambda title, msg: notify.osascript("relay run", title, msg)
                                   if cfg.get("notify") else None)
        self.project = config.project_root(os.path.dirname(os.path.abspath(plan_path)))
        with open(plan_path, encoding="utf-8") as f:
            self.plan_text = f.read()
        self.plan = planlib.parse(self.plan_text)
        self.id = run_id or datetime.datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
        self.dir = os.path.join(runs_dir(), self.id)
        self.approved = approved
        self.active_seconds = 0.0
        self.proc = None
        self._validate()

    def _validate(self):
        steps = self.plan["steps"]
        if len(steps) > int(self.rc["max_steps"]):
            raise planlib.PlanError(f"{len(steps)} steps; the cap is {self.rc['max_steps']} (run.max_steps)")
        enabled = [a for a in self.rc["agents"] if a == "codex"]
        if not enabled:
            raise planlib.PlanError("no usable agent: v1 runs Codex only (run.agents)")
        for s in steps:
            if s["agent"] and s["agent"] not in enabled:
                raise planlib.PlanError(f"step {s['n']} is pinned to {s['agent']}, which this version cannot run")
            for tier in TIERS[TIERS.index(s["tier"]):]:
                if not ((self.rc["tiers"].get("codex") or {}).get(tier) or {}).get("model"):
                    raise planlib.PlanError(f"no model for tier {tier} in run.tiers.codex")
        try:
            _git(self.project, "rev-parse", "HEAD")
        except RuntimeError as e:
            raise planlib.PlanError(f"{self.project} is not a git repository with a commit: {e}")

    # state
    def save(self):
        os.makedirs(self.dir, exist_ok=True)
        tmp = os.path.join(self.dir, "run.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)
        os.replace(tmp, os.path.join(self.dir, "run.json"))

    def say(self, msg):
        print(f"[{datetime.datetime.now():%H:%M:%S}] {msg}", file=self.out, flush=True)

    def _ctl(self, name):
        return os.path.exists(os.path.join(self.dir, name))

    def _check_stop(self):
        if self._ctl("stop"):
            raise Stopped("stopped by the owner")
        if self.active_seconds > float(self.rc["max_minutes"]) * 60:
            raise Stopped(f"time cap reached ({self.rc['max_minutes']} min, run.max_minutes)")

    def _wait(self, gate, until=None):
        """Block at a gate until approved (or until `until` for a quota wait). Waiting time is not capped."""
        self.state["status"], self.state["gate"] = "waiting", gate
        self.save()
        while True:
            if self._ctl("stop"):
                raise Stopped("stopped by the owner")
            if until is None and self._ctl(f"approve-{gate['id']}"):
                break
            if until is not None and time.time() >= until:
                break
            time.sleep(POLL)
        self.state["status"], self.state["gate"] = "running", None
        self.save()

    def start(self):
        os.makedirs(self.dir, exist_ok=True)
        with open(os.path.join(self.dir, "plan.md"), "w", encoding="utf-8") as f:
            f.write(self.plan_text)
        wt = os.path.join(self.dir, "worktree")
        branch = f"relay/run-{self.id}"
        base = _git(self.project, "rev-parse", "HEAD")
        dirty = bool(_git(self.project, "status", "--porcelain", "--untracked-files=no"))
        self.state = {
            "id": self.id, "pid": os.getpid(), "project": self.project, "plan_title": self.plan["title"],
            "branch": branch, "worktree": wt, "base": base, "created": now_iso(), "updated": now_iso(),
            "status": "running", "gate": None, "error": None, "current": None,
            "caps": {k: self.rc[k] for k in ("max_steps", "max_minutes", "max_retries", "step_timeout")},
            "steps": [{k: s[k] for k in ("n", "title", "tier", "confirm", "agent", "checks", "files")}
                      | {"status": "pending", "attempts": [], "summary": None, "diffstat": None, "commit": None}
                      for s in self.plan["steps"]],
        }
        self.save()
        self.say(f"run {self.id}: {len(self.plan['steps'])} steps from {self.plan['title']!r}")
        if dirty:
            self.say("note: the project has uncommitted changes; the worktree starts from HEAD without them")
        try:
            if not self.approved:
                self.say(f"waiting for plan approval: relay run approve {self.id}  (or the app)")
                self.notify("Plan waiting for approval", self.plan["title"])
                self._wait({"id": "plan", "kind": "plan"})
            _git(self.project, "worktree", "add", "-b", branch, wt, base)
            prev = None
            for step in self.plan["steps"]:
                prev = self._step(step, prev)
            self.state["status"] = "done"
            self.say(f"done: branch {branch} has {len(self.plan['steps'])} commits; merge it when ready")
            self.notify("Run finished", f"{self.plan['title']}: branch {branch}")
        except Stopped as e:
            self.state["status"], self.state["error"] = "stopped", str(e)
            self.say(f"stopped: {e}")
            self.notify("Run stopped", str(e))
        except Exception as e:  # noqa: BLE001 - the run must end with a recorded state
            self.state["status"], self.state["error"] = "failed", str(e)
            self.say(f"failed: {e}")
            self.notify("Run failed", str(e))
        finally:
            self._kill()
            self.state["current"], self.state["gate"] = None, None
            self.state["updated"] = now_iso()
            self.save()
        return 0 if self.state["status"] == "done" else 1

    def _step(self, step, prev_summary):
        st = self.state["steps"][step["n"] - 1]
        self._check_stop()
        if step["confirm"]:
            st["status"] = "waiting"
            self.say(f"step {step['n']} needs confirmation: relay run approve {self.id}")
            self.notify(f"Step {step['n']} waits for you", step["title"])
            self._wait({"id": f"step-{step['n']}", "kind": "confirm", "step": step["n"]})
        st["status"] = "running"
        failure = None
        start_tier = TIERS.index(step["tier"])
        for tier in TIERS[start_tier:]:
            for _ in range(int(self.rc["max_retries"]) + 1):
                self._check_stop()
                self._quota_gate()
                ok, failure = self._attempt(step, st, tier, prev_summary, failure)
                if ok:
                    st["status"] = "done"
                    self.save()
                    return st["summary"]
            if tier != TIERS[-1]:
                self.say(f"step {step['n']}: tier {tier} used up its attempts, going up")
        st["status"] = "failed"
        raise RuntimeError(f"step {step['n']} ({step['title']}) failed after every attempt: {_last_line(failure)}")

    def _quota_gate(self):
        rl = self.state.get("quota")
        if rl is None:
            from relay import watch  # lazy: watch imports a lot
            rl = watch.read_codex_quota()
        until = quota_wait(rl, self.rc)
        if until:
            self.say(f"Codex quota high; waiting until {datetime.datetime.fromtimestamp(until):%H:%M}")
            self.notify("Run waits for Codex quota", f"until {datetime.datetime.fromtimestamp(until):%H:%M}")
            self._wait({"id": "quota", "kind": "quota", "until": until}, until=until)
            self.state["quota"] = None

    def _attempt(self, step, st, tier, prev_summary, failure):
        n = len(st["attempts"]) + 1
        choice = self.rc["tiers"]["codex"][tier]
        model, effort = choice["model"], choice.get("effort")
        a = {"n": n, "agent": "codex", "tier": tier, "model": model, "effort": effort, "started": now_iso(),
             "ended": None, "result": None, "error": None, "usage": None, "thread_id": None, "checks": []}
        st["attempts"].append(a)
        self.state["current"] = {"step": step["n"], "total": len(self.plan["steps"]), "title": step["title"],
                                 "agent": "codex", "model": model, "effort": effort, "tier": tier, "attempt": n}
        self.save()
        self.say(f"step {step['n']}/{len(self.plan['steps'])} {step['title']!r}: codex {model} {effort or ''} "
                 f"attempt {n}")
        wt = self.state["worktree"]
        base = os.path.join(self.dir, f"step{step['n']}-a{n}")
        prompt = step_prompt(self.plan, step, prev_summary, failure, len(self.plan["steps"]))
        with open(base + ".prompt.md", "w", encoding="utf-8") as f:
            f.write(prompt)
        cmd = codex_command(self.rc, model, effort, wt, base + ".last.md", prompt)
        code, timed_out = self._spawn(cmd, wt, base + ".jsonl", base + ".err", float(self.rc["step_timeout"]))
        ev = read_codex_events(base + ".jsonl")
        a.update(thread_id=ev["thread_id"], usage=ev["usage"], ended=now_iso())
        self.state["quota"] = codex_rate_limits(ev["thread_id"]) or self.state.get("quota")
        try:
            with open(base + ".last.md", encoding="utf-8") as f:
                last = f.read()
        except OSError:
            last = ev["messages"][-1] if ev["messages"] else ""

        def fail(kind, text):
            a["result"], a["error"] = kind, _tail(text, 1500)
            self.save()
            self.say(f"  {kind}: {_last_line(text)}")
            return False, text

        if ev["error"] and FATAL.search(ev["error"]):
            a["result"], a["error"] = "agent_error", ev["error"]
            self.save()
            raise RuntimeError(f"Codex refused the run: {ev['error']} — check run.tiers / run.codex_bin")
        if ev["error"] and QUOTA.search(ev["error"]):
            a["result"], a["error"] = "quota", ev["error"]
            self.save()
            raise Stopped(f"Codex quota exhausted: {ev['error']}")
        if timed_out:
            return fail("timeout", f"The agent did not finish within {self.rc['step_timeout']} s.")
        if not ev["completed"]:
            with open(base + ".err", encoding="utf-8", errors="replace") as f:
                err = f.read()
            return fail("agent_failed", ev["error"] or f"codex exited {code}: {_tail(err, 1500)}")

        # checks
        agent_paths = _dirty(wt)
        outputs = []
        for c in step["checks"]:
            self._check_stop()
            r = self._run_check(c, wt)
            a["checks"].append({"command": c, "exit": r[0]})
            if r[0] != 0:
                outputs.append(f"$ {c}\nexit {r[0]}\n{_tail(r[1])}")
        with open(base + ".checks.txt", "w", encoding="utf-8") as f:
            f.write("\n\n".join(outputs) or "all checks passed\n")
        _undo_check_output(wt, agent_paths)
        _git(wt, "add", "-A")
        changed = [p for p in _git(wt, "diff", "--cached", "--name-only").splitlines() if p]
        outside = [p for p in changed if not planlib.allowed(p, step["files"])]
        if outputs:
            return fail("checks_failed", "Checks failed:\n" + "\n\n".join(outputs))
        if not changed:
            return fail("no_changes", "The step changed no files; the dispatcher needs a non-empty diff.")
        if outside:
            return fail("outside_files", "Changed files outside the allowed list (" + ", ".join(step["files"])
                        + "): " + ", ".join(outside) + ". Revert those changes.")

        st["summary"] = summary_from(last) or "(the agent wrote no step summary)"
        st["diffstat"] = _git(wt, "diff", "--cached", "--shortstat")
        msg = (f"relay step {step['n']}: {step['title']}\n\n{st['summary']}\n\n"
               f"relay run {self.id} · codex {model} {effort or ''} · attempt {n}")
        ident = [] if _git(wt, "config", "user.email", check=False) else \
            ["-c", "user.name=relay", "-c", "user.email=relay@localhost"]
        subprocess.run(["git", "-C", wt, *ident, "commit", "-q", "--no-verify", "-m", msg], check=True,
                       capture_output=True)
        st["commit"] = _git(wt, "rev-parse", "--short", "HEAD")
        a["result"] = "passed"
        self.say(f"  passed: {st['commit']} {st['diffstat']}")
        self.save()
        return True, None

    def _spawn(self, cmd, cwd, out_path, err_path, timeout):
        t0 = time.time()
        with open(out_path, "w") as out, open(err_path, "w") as err:
            try:
                self.proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                             start_new_session=True, env=_env())
            except OSError as e:
                raise RuntimeError(f"cannot start {cmd[0]}: {e}")
            try:
                while self.proc.poll() is None:
                    if self._ctl("stop"):
                        raise Stopped("stopped by the owner")
                    if time.time() - t0 > timeout:
                        self._kill()
                        return None, True
                    time.sleep(POLL)
                return self.proc.returncode, False
            finally:
                self.active_seconds += time.time() - t0

    def _run_check(self, command, cwd):
        t0 = time.time()
        try:
            p = subprocess.run(["/bin/sh", "-c", command], cwd=cwd, capture_output=True, text=True, env=_env(),
                               timeout=float(self.rc["check_timeout"]), stdin=subprocess.DEVNULL)
            return p.returncode, (p.stdout + p.stderr)
        except subprocess.TimeoutExpired:
            return 124, f"timed out after {self.rc['check_timeout']} s"
        finally:
            self.active_seconds += time.time() - t0

    def _kill(self):
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGTERM)
                p.wait(10)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except OSError:
                    pass


# --- plan drafting ----------------------------------------------------------------------------------
def draft(cfg, task, project, force=False, out=None):
    """A strong model writes .relay/plan.md from a short task; the owner reads it before `relay run`."""
    out = out or sys.stdout
    rc = dict(config.DEFAULTS["run"], **(cfg.get("run") or {}))
    path = os.path.join(project, ".relay", "plan.md")
    if os.path.exists(path) and not force:
        print(f"{path} exists; pass --force to replace it", file=out)
        return 1
    choice = rc["tiers"]["codex"]["strong"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    scratch = os.path.join(config.state_dir(), "plan-drafts")
    os.makedirs(scratch, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    last = os.path.join(scratch, f"{stamp}.md")
    cmd = [rc.get("codex_bin") or "codex", "exec", "--json", "-m", choice["model"], "-s", "read-only",
           "-C", project, "-o", last]
    if choice.get("effort"):
        cmd += ["-c", f"model_reasoning_effort={choice['effort']}"]
    cmd.append(planlib.DRAFT_PROMPT.format(task=task.strip(), max_steps=rc["max_steps"]))
    print(f"drafting with codex {choice['model']} {choice.get('effort') or ''}…", file=out, flush=True)
    with open(os.path.join(scratch, f"{stamp}.jsonl"), "w") as log:
        p = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.DEVNULL)
    ev = read_codex_events(os.path.join(scratch, f"{stamp}.jsonl"))
    try:
        with open(last, encoding="utf-8") as f:
            text = f.read().strip()
    except OSError:
        text = ""
    if not text:
        print(f"no plan: {ev['error'] or f'codex exited {p.returncode}'}", file=out)
        return 1
    m = re.search(r"^# ", text, re.M)  # drop any chatter before the title
    text = text[m.start():] if m else text
    with open(path, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    try:
        n = len(planlib.parse(text)["steps"])
        print(f"wrote {path} ({n} steps). Read it, edit it, then: relay run start", file=out)
        return 0
    except planlib.PlanError as e:
        print(f"wrote {path}, but it does not parse: {e}. Fix it before `relay run start`.", file=out)
        return 1


def print_runs(runs, out=None):
    out = out or sys.stdout
    if not runs:
        print("no runs", file=out)
    for r in runs:
        done = sum(1 for s in r.get("steps") or [] if s.get("status") == "done")
        cur = r.get("current") or {}
        where = f"step {cur['step']}/{cur['total']} · {cur['model']} · attempt {cur['attempt']}" if cur else \
            (r.get("error") or "")
        gate = (r.get("gate") or {}).get("id")
        print(f"{r['id']}  {r['status']:8} {done}/{len(r.get('steps') or [])}  {r.get('plan_title', '')[:40]:40} "
              f"{('waits: ' + gate) if gate else where}", file=out)
    return 0


def remove(run_id):
    """Drop a finished run's worktree (the branch stays for the owner)."""
    r = load(run_id)
    if not r or r.get("status") not in FINAL:
        return False
    if r.get("worktree") and os.path.isdir(r["worktree"]):
        _git(r["project"], "worktree", "remove", "--force", r["worktree"], check=False)
    shutil.rmtree(os.path.join(runs_dir(), run_id), ignore_errors=True)
    return True
