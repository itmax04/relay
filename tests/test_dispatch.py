"""Dispatcher tests: plan parsing, and whole runs against a scripted fake `codex exec --json`."""
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from relay import config, dispatch, plan  # noqa: E402

# Plays one scripted turn per call. Event lines copy the shape of a real codex-cli 0.144.6 run
# (2026-11-03): thread.started, item.completed agent_message, turn.completed with usage.
FAKE_CODEX = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if args[:1] == ["--version"]:
    print("codex-cli 0.0.0-fake"); sys.exit(0)
if args[:2] == ["login", "status"]:
    print("Logged in using ChatGPT"); sys.exit(int(os.environ.get("FAKE_LOGIN_RC", "0")))
cwd = args[args.index("-C") + 1]; last = args[args.index("-o") + 1]; model = args[args.index("-m") + 1]
state = os.environ["FAKE_CODEX_STATE"]
script = json.load(open(os.path.join(state, "script.json")))
n = len(os.listdir(os.path.join(state, "calls")))
open(os.path.join(state, "calls", f"{n:03}.json"), "w").write(json.dumps({"args": args, "model": model}))
turn = script[min(n, len(script) - 1)]
print(json.dumps({"type": "thread.started", "thread_id": f"thread-{n}"}), flush=True)
print(json.dumps({"type": "turn.started"}), flush=True)
if turn.get("sleep"):
    import time; time.sleep(turn["sleep"])
if turn.get("fail"):
    err = json.dumps({"type": "error", "status": 400, "error": {"type": "invalid_request_error",
                      "message": turn["fail"], "code": turn.get("code")}})
    print(json.dumps({"type": "error", "message": err}))
    print(json.dumps({"type": "turn.failed", "error": {"message": err}}))
    sys.exit(1)
for path, text in (turn.get("write") or {}).items():
    full = os.path.join(cwd, path); os.makedirs(os.path.dirname(full), exist_ok=True)
    open(full, "w").write(text)
msg = turn.get("message", "Done.\n\nStep summary: did the thing.")
open(last, "w").write(msg)
print(json.dumps({"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": msg}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 50,
                  "output_tokens": 10, "reasoning_output_tokens": 0}}))
'''


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, check=True).stdout.strip()


class PlanParseTest(unittest.TestCase):
    TEXT = textwrap.dedent("""\
        # Add greeting

        Python 3.9, no deps.

        ## 1. Write the file [light]
        Goal: a.txt says hello.
        Check: `grep -q hello a.txt`
        Files: a.txt, docs/

        ```
        ## not a step
        Check: not a check
        ```

        ## Step 2: Review it [strong] [confirm] [agent: codex]
        Done when: b exists.
        Check: test -f b.txt
        Check: `true`
        """)

    def test_parse(self):
        p = plan.parse(self.TEXT)
        self.assertEqual(p["title"], "Add greeting")
        self.assertEqual(p["context"], "Python 3.9, no deps.")
        s1, s2 = p["steps"]
        self.assertEqual((s1["n"], s1["title"], s1["tier"], s1["confirm"]), (1, "Write the file", "light", False))
        self.assertEqual(s1["checks"], ["grep -q hello a.txt"])
        self.assertEqual(s1["files"], ["a.txt", "docs/"])
        self.assertIn("## not a step", s1["body"])
        self.assertIn("Goal: a.txt says hello.", s1["body"])
        self.assertEqual((s2["title"], s2["tier"], s2["confirm"], s2["agent"]), ("Review it", "strong", True, "codex"))
        self.assertEqual(s2["checks"], ["test -f b.txt", "true"])

    def test_errors(self):
        with self.assertRaises(plan.PlanError):
            plan.parse("# Only a title\n\nno steps")
        with self.assertRaises(plan.PlanError):
            plan.parse("## 1. Thing [huge]\n")

    def test_allowed(self):
        pats = ["a.txt", "docs/", "src", "*.md", "./tests/"]
        for p in ("a.txt", "docs/x/y.py", "src/m.py", "README.md", "tests/t.py"):
            self.assertTrue(plan.allowed(p, pats), p)
        for p in ("b.txt", "srcx/m.py", "docsy"):
            self.assertFalse(plan.allowed(p, pats), p)
        self.assertTrue(plan.allowed("anything", []))


class PreambleTest(unittest.TestCase):
    def test_copy_setup_baseline(self):
        p = plan.parse(textwrap.dedent("""\
            # T
            Some context.
            Copy: .env, node_modules/
            Setup: `npm ci`
            Setup: make deps
            Baseline: `make test`

            ## 1. A
            Copy: not-a-preamble-line
            """))
        self.assertEqual(p["copy"], [".env", "node_modules/"])
        self.assertEqual(p["setup"], ["npm ci", "make deps"])
        self.assertEqual(p["baseline"], ["make test"])
        self.assertEqual(p["context"], "Some context.")
        self.assertIn("Copy: not-a-preamble-line", p["steps"][0]["body"])

    def test_fingerprint_and_env_failure(self):
        self.assertEqual(dispatch.fingerprint("checks_failed", ["t"], "a\n  b   c\n"),
                         dispatch.fingerprint("checks_failed", ["t"], "a\nb c"))
        self.assertNotEqual(dispatch.fingerprint("checks_failed", ["t"], "a"), dispatch.fingerprint("x", ["t"], "a"))
        self.assertTrue(dispatch.env_failure(127, ""))
        self.assertTrue(dispatch.env_failure(1, "/bin/sh: foo: command not found\n"))
        self.assertFalse(dispatch.env_failure(1, "FAIL: test_x\nNo such file or directory: 'x'"))

    def test_reset_time(self):
        self.assertEqual(dispatch.reset_time({"primary": {"used_percent": 100, "resets_at": 2000},
                                              "secondary": {"used_percent": 10, "resets_at": 9000}}, 1000), 2000)
        self.assertIsNone(dispatch.reset_time({"primary": {"used_percent": 50, "resets_at": 2000}}, 1000))
        self.assertIsNone(dispatch.reset_time(None))


class HelpersTest(unittest.TestCase):
    def test_quota_wait(self):
        now = 1000.0
        rc = {"quota_switch_at": 80, "quota_week_stop": 98}
        self.assertIsNone(dispatch.quota_wait(None, rc, now))
        self.assertIsNone(dispatch.quota_wait({"primary": {"used_percent": 50, "resets_at": 2000}}, rc, now))
        self.assertEqual(dispatch.quota_wait({"primary": {"used_percent": 85, "resets_at": 2000}}, rc, now), 2000)
        # a window that already reset says nothing about now
        self.assertIsNone(dispatch.quota_wait({"primary": {"used_percent": 99, "resets_at": 500}}, rc, now))
        self.assertEqual(dispatch.quota_wait({"primary": {"used_percent": 5, "resets_at": 2000},
                                              "secondary": {"used_percent": 99, "resets_at": 9000}}, rc, now), 9000)

    def test_summary_and_error_text(self):
        self.assertEqual(dispatch.summary_from("blah\n\n**Step summary:** Added x.\nMore."), "Added x.\nMore.")
        self.assertEqual(dispatch.summary_from("no block"), "")
        raw = json.dumps({"type": "error", "status": 400, "error": {
            "message": "The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account.",
            "code": "model_not_found"}})
        self.assertTrue(dispatch.FATAL.search(dispatch._error_text(raw)))

    def test_rate_limits_from_rollout(self):
        with tempfile.TemporaryDirectory() as root:
            d = os.path.join(root, "2026", "11", "03")
            os.makedirs(d)
            with open(os.path.join(d, "rollout-2026-11-03T18-02-26-abc-123.jsonl"), "w") as f:
                rl = {"limit_id": "codex", "primary": {"used_percent": 5.0, "window_minutes": 300,
                                                       "resets_at": 1793732648}}
                f.write(json.dumps({"timestamp": "t1", "type": "event_msg",
                                    "payload": {"type": "token_count", "rate_limits": rl}}) + "\n")
            got = dispatch.codex_rate_limits("abc-123", root)
            self.assertEqual(got["primary"]["used_percent"], 5.0)
            self.assertIsNone(dispatch.codex_rate_limits("other", root))


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.env = {k: os.environ.get(k) for k in ("RELAY_STATE_DIR", "FAKE_CODEX_STATE")}
        os.environ["RELAY_STATE_DIR"] = os.path.join(t, "state")
        os.environ["FAKE_CODEX_STATE"] = self.fake = os.path.join(t, "fake")
        os.makedirs(os.path.join(self.fake, "calls"))
        self.bin = os.path.join(t, "codex")
        with open(self.bin, "w") as f:
            f.write(FAKE_CODEX)
        os.chmod(self.bin, 0o755)
        self.repo = os.path.join(t, "repo")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "t")
        with open(os.path.join(self.repo, "README"), "w") as f:
            f.write("x\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "init")
        self.notes = []

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        try:
            git(self.repo, "worktree", "prune")
        except subprocess.CalledProcessError:
            pass
        self.tmp.cleanup()

    def script(self, turns):
        with open(os.path.join(self.fake, "script.json"), "w") as f:
            json.dump(turns, f)

    def calls(self):
        d = os.path.join(self.fake, "calls")
        out = []
        for n in sorted(os.listdir(d)):
            with open(os.path.join(d, n)) as f:
                out.append(json.load(f))
        return out

    def make_run(self, text, approved=True, **run_cfg):
        path = os.path.join(self.repo, "plan.md")
        with open(path, "w") as f:
            f.write(textwrap.dedent(text))
        cfg = config.load(self.repo)
        cfg["notify"] = False
        opts = dict(codex_bin=self.bin, preflight=False, caffeinate=False, min_free_gb=0)
        cfg["run"] = dict(cfg["run"], **{**opts, **run_cfg})
        r = dispatch.Run(cfg, path, approved=approved, out=io.StringIO(),
                         notifier=lambda t, m: self.notes.append(t))
        r._quota_gate = lambda: None  # the real gate reads this Mac's Codex rollouts
        return r

    def test_two_steps_commit_and_hand_over_summary(self):
        self.script([{"write": {"a.txt": "hello\n"}, "message": "ok\nStep summary: a.txt has hello."},
                     {"write": {"b.txt": "bye\n"}}])
        r = self.make_run("""\
            # Greeting
            ## 1. Hello [light]
            Check: grep -q hello a.txt
            Files: a.txt
            ## 2. Bye
            Check: test -f b.txt
            """)
        self.assertEqual(r.start(), 0)
        st = dispatch.load(r.id)
        self.assertEqual(st["status"], "done")
        self.assertEqual([s["status"] for s in st["steps"]], ["done", "done"])
        self.assertEqual(st["steps"][0]["summary"], "a.txt has hello.")
        branch = st["branch"]
        log = git(self.repo, "log", "--format=%s", branch)
        self.assertEqual(log.splitlines()[:2], ["relay step 2: Bye", "relay step 1: Hello"])
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), st["base"])  # main untouched
        calls = self.calls()
        self.assertEqual([c["model"] for c in calls], ["gpt-5.6-luna", "gpt-5.6-sol"])
        self.assertIn("a.txt has hello.", calls[1]["args"][-1])
        self.assertIn("-s", calls[0]["args"])
        self.assertEqual(calls[0]["args"][calls[0]["args"].index("-s") + 1], "workspace-write")
        self.assertIn("Run finished", self.notes)

    def test_retry_with_error_then_escalate(self):
        self.script([{"write": {"a.txt": "nope\n"}}, {"write": {"a.txt": "still no\n"}},
                     {"write": {"a.txt": "hello\n"}}])
        r = self.make_run("""\
            # G
            ## 1. Hello [light]
            Check: grep -q hello a.txt
            """, max_retries=1)
        self.assertEqual(r.start(), 0)
        calls = self.calls()
        self.assertEqual([c["model"] for c in calls], ["gpt-5.6-luna", "gpt-5.6-luna", "gpt-5.6-sol"])
        self.assertIn("previous attempt at this step failed", calls[1]["args"][-1])
        self.assertIn("grep -q hello a.txt", calls[1]["args"][-1])
        st = dispatch.load(r.id)
        self.assertEqual([a["result"] for a in st["steps"][0]["attempts"]],
                         ["checks_failed", "checks_failed", "passed"])
        self.assertEqual(st["steps"][0]["attempts"][2]["tier"], "standard")

    def test_outside_files_and_empty_diff_fail(self):
        self.script([{"write": {"other.txt": "x"}}, {}])
        r = self.make_run("""\
            # G
            ## 1. Hello [strong]
            Files: a.txt
            """, max_retries=1)
        self.assertEqual(r.start(), 1)
        st = dispatch.load(r.id)
        self.assertEqual(st["status"], "failed")
        self.assertEqual([a["result"] for a in st["steps"][0]["attempts"]], ["outside_files", "outside_files"])
        self.assertIn("other.txt", st["steps"][0]["attempts"][0]["error"])
        self.assertIn("Run failed", self.notes)

    def test_files_written_by_checks_are_not_the_agents(self):
        # Live run 2026-11-03: `python3 -c "import greet"` left __pycache__/*.pyc and the step was
        # failed as "outside the allowed files" on every attempt.
        self.script([{"write": {"a.txt": "hello\n"}}])
        r = self.make_run("""\
            # G
            ## 1. Hello
            Check: mkdir -p cache && echo junk > cache/x.bin && echo more >> README
            Files: a.txt
            """)
        self.assertEqual(r.start(), 0)
        st = dispatch.load(r.id)
        self.assertEqual(git(self.repo, "show", "--name-only", "--format=", st["branch"]), "a.txt")
        self.assertEqual(len(self.calls()), 1)

    def test_refused_model_stops_without_retries(self):
        self.script([{"fail": "The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT "
                              "account.", "code": "model_not_found"}])
        r = self.make_run("# G\n## 1. Hello\nCheck: true\n")
        self.assertEqual(r.start(), 1)
        st = dispatch.load(r.id)
        self.assertEqual(len(self.calls()), 1)
        self.assertIn("model_not_found", st["error"])

    def test_plan_and_confirm_gates_then_stop(self):
        self.script([{"write": {"a.txt": "1"}}, {"write": {"b.txt": "2"}}])
        r = self.make_run("""\
            # G
            ## 1. One
            ## 2. Two [confirm]
            """, approved=False)
        t = threading.Thread(target=r.start)
        t.start()

        def wait_gate(gid):
            for _ in range(100):
                st = dispatch.load(r.id)
                if st and (st.get("gate") or {}).get("id") == gid:
                    return st
                time.sleep(0.1)
            self.fail(f"never waited at {gid}")

        self.assertEqual(wait_gate("plan")["status"], "waiting")
        self.assertEqual(len(self.calls()), 0)
        self.assertEqual(dispatch.approve(r.id), "plan")
        wait_gate("step-2")
        self.assertEqual(len(self.calls()), 1)
        self.assertTrue(dispatch.request_stop(r.id))
        t.join(10)
        st = dispatch.load(r.id)
        self.assertEqual(st["status"], "stopped")
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(dispatch.list_runs()[0]["id"], r.id)
        self.assertTrue(dispatch.remove(r.id))
        self.assertIsNone(dispatch.load(r.id))

    def test_stop_kills_running_agent(self):
        self.script([{"sleep": 30, "write": {"a.txt": "1"}}])
        r = self.make_run("# G\n## 1. Slow\n")
        t = threading.Thread(target=r.start)
        t.start()
        for _ in range(50):
            if self.calls():
                break
            time.sleep(0.1)
        t0 = time.time()
        dispatch.request_stop(r.id)
        t.join(15)
        self.assertLess(time.time() - t0, 10)
        self.assertEqual(dispatch.load(r.id)["status"], "stopped")

    def test_validation(self):
        with self.assertRaises(plan.PlanError):
            self.make_run("# G\n## 1. A [agent: claude]\n")
        with self.assertRaises(plan.PlanError):
            self.make_run("# G\n## 1. A\n## 2. B\n", max_steps=1)

    # --- v1.1 -----------------------------------------------------------------------------------
    def ignore(self, *patterns):
        with open(os.path.join(self.repo, ".gitignore"), "w") as f:
            f.write("\n".join(patterns) + "\nplan.md\n")
        git(self.repo, "add", ".gitignore")
        git(self.repo, "commit", "-qm", "ignore")

    def test_copy_setup_baseline_then_cleanup(self):
        self.ignore(".env", "deps/")
        with open(os.path.join(self.repo, ".env"), "w") as f:
            f.write("KEY=1\n")
        os.makedirs(os.path.join(self.repo, "deps"))
        with open(os.path.join(self.repo, "deps", "lib"), "w") as f:
            f.write("lib\n")
        self.script([{"write": {"a.txt": "hello\n"}}])
        r = self.make_run("""\
            # G
            Copy: .env, deps/
            Setup: touch deps/installed
            Baseline: test -f .env && test -f deps/lib && test -f deps/installed
            ## 1. Hello
            Check: grep -q KEY=1 .env && grep -q hello a.txt
            Files: a.txt
            """)
        self.assertEqual(r.start(), 0, r.out.getvalue())
        st = dispatch.load(r.id)
        self.assertEqual(st["status"], "done")
        self.assertEqual(git(self.repo, "show", "--name-only", "--format=", st["branch"]), "a.txt")
        self.assertTrue(st["worktree_removed"])
        self.assertFalse(os.path.isdir(st["worktree"]))
        self.assertIn("exit 0", open(os.path.join(r.dir, "setup.txt")).read())

    def test_cleanup_never_keeps_worktree(self):
        self.script([{"write": {"a.txt": "x\n"}}])
        r = self.make_run("# G\n## 1. A\n", cleanup="never")
        self.assertEqual(r.start(), 0)
        self.assertTrue(os.path.isdir(dispatch.load(r.id)["worktree"]))

    def test_baseline_failure_makes_no_agent_call(self):
        self.script([{"write": {"a.txt": "x\n"}}])
        r = self.make_run("""\
            # G
            Baseline: echo build is broken; exit 3
            ## 1. A
            """)
        self.assertEqual(r.start(), 1)
        st = dispatch.load(r.id)
        self.assertEqual(st["status"], "failed")
        self.assertIn("baseline failed", st["error"])
        self.assertEqual(len(self.calls()), 0)
        self.assertIn("build is broken", open(os.path.join(r.dir, "setup.txt")).read())

    def test_copy_problems_and_setup_leftovers_fail_before_agents(self):
        self.script([{"write": {"a.txt": "x\n"}}])
        r = self.make_run("# G\nCopy: missing.env\n## 1. A\n")
        self.assertEqual(r.start(), 1)
        self.assertIn("does not exist", dispatch.load(r.id)["error"])
        with open(os.path.join(self.repo, "tracked-looking"), "w") as f:
            f.write("x")  # exists but is not git-ignored
        r = self.make_run("# G\nCopy: tracked-looking\n## 1. A\n")
        self.assertEqual(r.start(), 1)
        self.assertIn("not git-ignored", dispatch.load(r.id)["error"])
        r = self.make_run("# G\nSetup: touch junk.txt\n## 1. A\n")
        self.assertEqual(r.start(), 1)
        self.assertIn("not git-ignored", dispatch.load(r.id)["error"])
        self.assertEqual(len(self.calls()), 0)

    def test_environment_failure_is_not_retried(self):
        self.script([{"write": {"a.txt": "x\n"}}])
        r = self.make_run("# G\n## 1. A\nCheck: relay-no-such-tool-xyz --version\n", max_retries=3)
        self.assertEqual(r.start(), 1)
        st = dispatch.load(r.id)
        self.assertTrue(st["error"].startswith("environment:"), st["error"])
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(st["steps"][0]["attempts"][0]["result"], "environment")

    def test_same_failure_twice_skips_the_rest_of_the_tier(self):
        self.script([{"write": {"a.txt": "nope\n"}}])
        r = self.make_run("# G\n## 1. A [light]\nCheck: echo same-error; exit 1\n", max_retries=3)
        self.assertEqual(r.start(), 1)
        self.assertEqual(len(self.calls()), 6)  # 2 attempts x light, standard, strong (not 4 x 3)

    def test_attempt_cap_stops_the_run(self):
        self.script([{"write": {"a.txt": "nope\n"}}])
        r = self.make_run("# G\n## 1. A\nCheck: exit 1\n", max_attempts=2)
        self.assertEqual(r.start(), 1)
        st = dispatch.load(r.id)
        self.assertEqual((st["status"], len(self.calls())), ("stopped", 2))
        self.assertIn("max_attempts", st["error"])

    def test_disk_guard_stops_before_worktree(self):
        self.script([{"write": {"a.txt": "x\n"}}])
        r = self.make_run("# G\n## 1. A\n", min_free_gb=10 ** 9)
        self.assertEqual(r.start(), 1)
        st = dispatch.load(r.id)
        self.assertEqual(st["status"], "stopped")
        self.assertIn("GB free", st["error"])
        self.assertEqual(len(self.calls()), 0)
        self.assertFalse(os.path.isdir(st["worktree"]))

    def test_quota_mid_step_waits_and_does_not_count(self):
        self.script([{"fail": "You have hit your usage limit.", "code": "usage_limit_reached"},
                     {"write": {"a.txt": "x\n"}}])
        r = self.make_run("# G\n## 1. A\n", max_retries=0, max_attempts=1)
        soon = time.time() + 4
        orig = dispatch.codex_rate_limits
        dispatch.codex_rate_limits = lambda tid, root=None: {"primary": {"used_percent": 100, "resets_at": soon}}
        try:
            self.assertEqual(r.start(), 0, r.out.getvalue())
        finally:
            dispatch.codex_rate_limits = orig
        st = dispatch.load(r.id)
        self.assertEqual([a["result"] for a in st["steps"][0]["attempts"]], ["quota", "passed"])
        self.assertIn("waiting until", r.out.getvalue())

    def test_quota_with_unknown_reset_stops(self):
        self.script([{"fail": "You have hit your usage limit.", "code": "usage_limit_reached"}])
        r = self.make_run("# G\n## 1. A\n")
        r._fresh_quota = lambda: None
        self.assertEqual(r.start(), 1)
        self.assertEqual(dispatch.load(r.id)["status"], "stopped")

    def resume_run(self):
        cfg = config.load(self.repo)
        cfg["notify"] = False
        cfg["run"] = dict(cfg["run"], codex_bin=self.bin, preflight=False, caffeinate=False, min_free_gb=0)
        return cfg

    def test_resume_continues_from_the_last_step_commit(self):
        flag = os.path.join(self.tmp.name, "flag")
        self.script([{"write": {"a.txt": "one\n"}}, {"write": {"b.txt": "two\n", "junk.txt": "j"}}])
        r = self.make_run(f"""\
            # G
            ## 1. One [light]
            ## 2. Two [light]
            Check: test -f {flag}
            """, max_retries=0)
        self.assertEqual(r.start(), 1)
        st = dispatch.load(r.id)
        self.assertEqual([s["status"] for s in st["steps"]], ["done", "failed"])
        n_calls = len(self.calls())
        self.assertEqual(n_calls, 4)  # step 1 once, step 2 on three tiers
        self.assertIsNone(dispatch.resume_problem(r.id))
        open(flag, "w").close()
        r2 = dispatch.Run(self.resume_run(), "", run_id=r.id, resume=True, out=io.StringIO(),
                          notifier=lambda t, m: None)
        r2._quota_gate = lambda: None
        self.assertEqual(r2.start(), 0, r2.out.getvalue())
        st = dispatch.load(r.id)
        self.assertEqual((st["status"], st["resumes"], len(self.calls())), ("done", 1, n_calls + 1))
        self.assertGreater(len(st["steps"][1]["attempts"]), 3)  # history kept
        self.assertEqual(git(self.repo, "log", "--format=%s", st["branch"]).splitlines()[:2],
                         ["relay step 2: Two", "relay step 1: One"])
        self.assertEqual(sorted(git(self.repo, "show", "--name-only", "--format=", st["branch"]).split()),
                         ["b.txt", "junk.txt"])
        self.assertIn("worktree reset", r2.out.getvalue())
        self.assertIsNotNone(dispatch.resume_problem(r.id))  # done: nothing to resume

    def test_resume_before_plan_approval_still_asks(self):
        self.script([{"write": {"a.txt": "x\n"}}])
        r = self.make_run("# G\n## 1. A\n", approved=False)
        t = threading.Thread(target=r.start)
        t.start()
        for _ in range(50):
            if (dispatch.load(r.id) or {}).get("gate"):
                break
            time.sleep(0.1)
        dispatch.request_stop(r.id)
        t.join(10)
        self.assertEqual(dispatch.load(r.id)["status"], "stopped")
        r2 = dispatch.Run(self.resume_run(), "", run_id=r.id, resume=True, out=io.StringIO(),
                          notifier=lambda t, m: None)
        r2._quota_gate = lambda: None
        t2 = threading.Thread(target=r2.start)
        t2.start()
        for _ in range(50):
            if (dispatch.load(r.id) or {}).get("gate"):
                break
            time.sleep(0.1)
        self.assertEqual(dispatch.load(r.id)["gate"]["id"], "plan")
        self.assertEqual(len(self.calls()), 0)
        dispatch.approve(r.id)
        t2.join(20)
        self.assertEqual(dispatch.load(r.id)["status"], "done")

    def test_resume_problems(self):
        self.assertIn("no run", dispatch.resume_problem("nope"))
        self.script([{"write": {"a.txt": "x\n"}}])
        r = self.make_run("# G\n## 1. A\n")
        r.start()
        self.assertEqual(dispatch.resume_problem(r.id), "the run is done")

    def test_preflight(self):
        cache = os.path.join(self.tmp.name, "models.json")
        with open(cache, "w") as f:
            json.dump({"models": [{"slug": "gpt-5.6-sol"}, {"slug": "gpt-5.6-luna"}]}, f)
        with open(os.path.join(self.repo, ".env"), "w") as f:
            f.write("x")
        self.ignore(".env")
        text = "# G\nCopy: .env\n## 1. A [light]\nCheck: true\n## 2. B\n"
        rc = dict(config.DEFAULTS["run"], codex_bin=self.bin, codex_models_cache=cache, min_free_gb=0)
        errors, warnings, notes = dispatch.preflight(rc, self.repo, plan.parse(text))
        self.assertEqual(errors, [])
        self.assertTrue(any("step 1 has no Files" in w for w in warnings))
        self.assertTrue(any("step 2 has no Check" in w for w in warnings))
        self.assertTrue(any("0.0.0-fake" in n for n in notes))
        bad = dict(rc, tiers={"codex": {t: {"model": "gpt-9"} for t in plan.TIERS}}, min_free_gb=10 ** 9,
                   codex_bin=os.path.join(self.tmp.name, "nope"))
        errors, _, _ = dispatch.preflight(bad, self.repo, plan.parse(text.replace(".env", "gone")))
        joined = "\n".join(errors)
        for needle in ("codex binary not found", "GB free", "Copy: gone"):
            self.assertIn(needle, joined)
        os.environ["FAKE_LOGIN_RC"] = "1"
        try:
            errors, _, _ = dispatch.preflight(rc, self.repo, plan.parse(text))
            self.assertTrue(any("not logged in" in e for e in errors))
            errors, _, _ = dispatch.preflight(dict(rc, tiers={"codex": {t: {"model": "gpt-9"} for t in plan.TIERS}}),
                                              self.repo, plan.parse(text))
            self.assertTrue(any("gpt-9" in e for e in errors))
        finally:
            del os.environ["FAKE_LOGIN_RC"]

    def test_failed_preflight_in_start_creates_nothing(self):
        r = self.make_run("# G\n## 1. A\n", preflight=True, codex_bin=os.path.join(self.tmp.name, "nope"))
        self.assertEqual(r.start(), 1)
        self.assertIsNone(dispatch.load(r.id))
        self.assertFalse(os.path.isdir(r.dir))


if __name__ == "__main__":
    unittest.main()
