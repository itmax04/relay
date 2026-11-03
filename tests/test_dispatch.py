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
        cfg["run"] = dict(cfg["run"], codex_bin=self.bin, **run_cfg)
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


if __name__ == "__main__":
    unittest.main()
