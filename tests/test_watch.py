import http.client
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from relay import advisor, catalog, config, live, notify, providers, rules, server, store, watch  # noqa: E402

T0 = 1_793_600_000.0  # a fixed "now" for the rules


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def assistant(i, ctx, tools=(), t=T0, effort="high", model="claude-opus-5-5", out=50):
    content = [{"type": "tool_use", "id": f"tu{i}-{n}", "name": name, "input": args}
               for n, (name, args) in enumerate(tools)] or [{"type": "text", "text": "ok"}]
    return {"type": "assistant", "sessionId": "s1", "requestId": f"r{i}", "timestamp": iso(t), "effort": effort,
            "cwd": "/work/app", "entrypoint": "claude-desktop",
            "message": {"id": f"m{i}", "model": model, "content": content, "usage": {
                "input_tokens": 5, "cache_creation_input_tokens": 100, "cache_read_input_tokens": ctx - 105,
                "output_tokens": out}}}


def result(i, text, error=False, n=0, t=T0):
    return {"type": "user", "sessionId": "s1", "timestamp": iso(t), "toolUseResult": {},
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"tu{i}-{n}",
                                                     "content": text, "is_error": error}]}}


def prompt(text, t=T0):
    return {"type": "user", "sessionId": "s1", "timestamp": iso(t), "message": {"role": "user", "content": text}}


def write_lines(path, lines, mode="w"):
    with open(path, mode) as f:
        for d in lines:
            f.write((d if isinstance(d, str) else json.dumps(d)) + "\n")


def session_from(agent, lines, tmp, name="s1.jsonl"):
    path = os.path.join(tmp, name)
    write_lines(path, lines)
    s = live.Session(agent, path)
    live.Tailer()._read(s, 0, os.path.getsize(path))
    return s


CFG = config.load("/nonexistent")
CAT = {"claude": catalog.claude_models("/nonexistent"),
       "codex": [{"slug": "gpt-6-astra", "name": "Astra", "tier": "strong", "efforts": ["low", "medium", "high"]},
                 {"slug": "gpt-6.1-sol", "name": "Sol", "tier": "standard", "efforts": ["low", "medium", "high"]}]}


class TmpCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()


class LiveTests(TmpCase):
    def test_claude_state(self):
        s = session_from("claude", [
            prompt("fix the login test"),
            assistant(1, 50_000, [("Bash", {"command": "pytest tests/test_login.py"})]),
            result(1, "Exit code 1\nFAILED test_login", error=True),
            {"type": "custom-title", "customTitle": "Login fix", "sessionId": "s1"},
        ], self.tmp)
        snap = s.snapshot(T0 + 60)
        self.assertEqual((snap["context"], snap["model"], snap["effort"], snap["turns"], snap["tool_calls"]),
                         (50_000, "claude-opus-5-5", "high", 1, 1))
        self.assertEqual((snap["title"], snap["failures"], snap["idle_seconds"]), ("Login fix", 1, 60))
        self.assertEqual(s.last_prompt, "fix the login test")

    def test_tail_reads_only_complete_new_lines(self):
        path = os.path.join(self.tmp, "proj", "s1.jsonl")
        os.makedirs(os.path.dirname(path))
        write_lines(path, [assistant(1, 10_000)])
        t = live.Tailer(discover=lambda agent, since: [path] if agent == "claude" else [])
        self.assertEqual(len(t.scan()), 1)
        s = t.sessions[path]
        with open(path, "a") as f:
            f.write(json.dumps(assistant(2, 20_000)) + "\n" + json.dumps(assistant(3, 30_000))[:40])
        self.assertEqual(t.poll(), [s])
        self.assertEqual(s.context, 20_000)
        with open(path, "a") as f:
            f.write(json.dumps(assistant(3, 30_000))[40:] + "\n")
        t.poll()
        self.assertEqual((s.context, s.calls), (30_000, 3))
        self.assertEqual(t.poll(), [])

    def test_subagent_files_are_not_sessions(self):
        path = os.path.join(self.tmp, "p", "s1", "subagents", "agent-1.jsonl")
        os.makedirs(os.path.dirname(path))
        write_lines(path, [assistant(1, 10_000)])
        t = live.Tailer(discover=lambda agent, since: [path] if agent == "claude" else [])
        self.assertEqual(t.scan(), [])

    def test_codex_state(self):
        rl = {"primary": {"used_percent": 40.0}, "secondary": {"used_percent": 70.0, "resets_at": T0 + 1e5}}
        s = session_from("codex", [
            {"type": "session_meta", "payload": {"session_id": "c1", "cwd": "/work", "originator": "codex_cli"}},
            {"type": "turn_context", "payload": {"model": "gpt-6-astra", "effort": "high"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                  "content": [{"type": "input_text", "text": "run tests"}]}},
            {"type": "response_item", "payload": {"type": "function_call", "name": "shell", "call_id": "c1",
                                                  "arguments": json.dumps({"cmd": "npm test"})}},
            {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1",
                                                  "output": "Process exited with code 1\nnpm ERR!"}},
            {"type": "event_msg", "timestamp": iso(T0), "payload": {
                "type": "token_count", "rate_limits": rl,
                "info": {"total_token_usage": {"total_tokens": 9},
                         "last_token_usage": {"input_tokens": 120_000, "cached_input_tokens": 100_000,
                                              "output_tokens": 7}}}},
        ], self.tmp)
        snap = s.snapshot(T0)
        self.assertEqual((snap["model"], snap["effort"], snap["context"], snap["turns"]),
                         ("gpt-6-astra", "high", 120_000, 1))
        self.assertTrue(s.recent_tools[-1]["error"])
        self.assertEqual(snap["rate_limits"]["secondary"]["used_percent"], 70.0)


class RuleTests(TmpCase):
    def rules_for(self, lines, now=T0 + 10, cfg=None, quotas=None):
        s = session_from("claude", lines, self.tmp)
        return {a["rule"]: a for a in rules.evaluate([s], quotas or {}, cfg or CFG, CAT, now)}

    def test_context_soft_hard_and_dormant(self):
        self.assertEqual(self.rules_for([assistant(1, 160_000)])["context"]["severity"], "warn")
        self.assertEqual(self.rules_for([assistant(1, 320_000)])["context"]["severity"], "high")
        dormant = self.rules_for([assistant(1, 320_000)], now=T0 + 5 * 3600)["context"]
        self.assertEqual((dormant["severity"], dormant["action"]), ("info", "new_session"))
        self.assertNotIn("context", self.rules_for([assistant(1, 90_000)]))

    def test_cache_expiring_then_expired(self):
        ttl = CFG["cache_ttl"]["claude"]
        self.assertEqual(self.rules_for([assistant(1, 120_000)], now=T0 + ttl - 120)["cache"]["severity"], "info")
        expired = self.rules_for([assistant(1, 120_000)], now=T0 + ttl + 60)["cache"]
        self.assertEqual((expired["severity"], expired["action"]), ("warn", "new_session"))
        self.assertNotIn("cache", self.rules_for([assistant(1, 50_000)], now=T0 + ttl + 60))

    def test_repeated_failure(self):
        lines = []
        for i in range(3):
            lines += [assistant(i, 60_000, [("Bash", {"command": "npm  test"})]),
                      result(i, "Exit code 1\nboom", error=True)]
        a = self.rules_for(lines)["repeat"]
        self.assertEqual((a["action"], a["severity"], a["metrics"]["failures"]), ("stop_retrying", "high", 3))
        self.assertIn("boom", a["metrics"]["error"])
        # the last attempt passed: no advice
        lines += [assistant(9, 60_000, [("Bash", {"command": "npm test"})]), result(9, "ok")]
        self.assertNotIn("repeat", self.rules_for(lines))

    def test_large_read(self):
        big = "x" * (live.CHARS_PER_TOKEN * 30_000)
        a = self.rules_for([assistant(1, 60_000, [("Read", {"file_path": "/w/big.log"})]), result(1, big)])
        self.assertEqual(a["large_read"]["action"], "read_narrowly")
        narrow = self.rules_for([assistant(1, 60_000, [("Read", {"file_path": "/w/big.log", "limit": 9})]),
                                 result(1, big)])
        self.assertNotIn("large_read", narrow)

    def test_mechanical_run_suggests_cheaper_model(self):
        lines = []
        for i in range(10):
            lines += [assistant(i, 60_000, [("Edit", {"file_path": f"/w/f{i}.py"})]), result(i, "ok")]
        a = self.rules_for(lines)["mechanical"]
        self.assertEqual((a["action"], a["suggested"]["model"]), ("switch_model", "claude-sonnet-5-5"))
        lines[-2] = assistant(9, 60_000, [("Bash", {"command": "python3 build.py --release"})])
        self.assertNotIn("mechanical", self.rules_for(lines))

    def test_mechanical_on_standard_model_at_high_effort(self):
        lines = [assistant(i, 60_000, [("Read", {"file_path": "/w/a"})], model="claude-sonnet-5-5")
                 for i in range(10)]
        a = self.rules_for(lines)["mechanical"]
        self.assertEqual((a["action"], a["suggested"]["effort"]), ("change_effort", "medium"))

    def test_quota(self):
        q = {"claude": {"seven_day": {"used_percentage": 92, "resets_at": T0 + 1e5}, "five_hour": {}}}
        a = self.rules_for([assistant(1, 60_000)], quotas=q)["quota"]
        self.assertEqual((a["severity"], a["suggested"]["model"]), ("high", "claude-sonnet-5-5"))
        stale = {"claude": {"seven_day": {"used_percentage": 92, "resets_at": T0 - 10}}}
        self.assertNotIn("quota", self.rules_for([assistant(1, 60_000)], quotas=stale))
        five = {"codex": {"primary": {"used_percent": 95, "resets_at": T0 + 600}, "secondary": {"used_percent": 10}}}
        self.assertEqual(self.rules_for([assistant(1, 60_000)], quotas=five)["quota"]["agent"], "codex")


    def test_quota_state(self):
        windows = ("five_hour", "seven_day")
        self.assertEqual(watch.quota_state(None, windows, T0), "no_reading")
        self.assertEqual(watch.quota_state(None, windows, T0, source_ready=False), "no_source")
        self.assertEqual(watch.quota_state({"ts": "x"}, windows, T0), "no_reading")
        live_week = {"five_hour": {"used_percentage": 40, "resets_at": T0 - 10},
                     "seven_day": {"used_percentage": 70, "resets_at": T0 + 1e5}}
        self.assertEqual(watch.quota_state(live_week, windows, T0), "ok")
        both_reset = {"five_hour": {"resets_at": T0 - 10}, "seven_day": {"resets_at": T0 - 5}}
        self.assertEqual(watch.quota_state(both_reset, windows, T0), "stale")


class CatalogTests(TmpCase):
    def test_codex_tiers_and_hidden_models(self):
        path = os.path.join(self.tmp, "models_cache.json")
        with open(path, "w") as f:
            json.dump({"models": [
                {"slug": "big", "visibility": "list", "description": "Frontier intelligence for hard work",
                 "supported_reasoning_levels": [{"effort": "high"}]},
                {"slug": "mid", "visibility": "list", "description": "Workhorse model"},
                {"slug": "small", "visibility": "list", "description": "Fast and affordable model for easier tasks"},
                {"slug": "secret", "visibility": "hide", "description": "x"}]}, f)
        models = catalog.codex_models(path)
        self.assertEqual([(m["slug"], m["tier"]) for m in models], [("big", "strong"), ("mid", "standard"),
                                                                    ("small", "light")])
        self.assertEqual(catalog.cheaper({"codex": models}, "codex", "big")["slug"], "mid")
        self.assertIsNone(catalog.cheaper({"codex": models}, "codex", "small"))


class FakeOpenAI:
    """Local OpenAI-compatible server; `script` maps a model to a list of (status, body) replies."""

    def __init__(self):
        self.script = {}
        self.seen = []
        self.bodies = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.seen.append((body["model"], self.headers["Authorization"][7:]))
                fake.bodies.append(body)
                replies = fake.script.get(body["model"]) or [(200, None)]
                status, payload = replies.pop(0) if len(replies) > 1 else replies[0]
                if status == 200 and payload is None:
                    payload = {"choices": [{"message": {"content": json.dumps({
                        "action": "switch_model", "severity": "warn", "model": "claude-sonnet-5-5",
                        "effort": "medium", "title": "Use Sonnet", "reason": "Edits only."})}}]}
                data = json.dumps(payload).encode()
                self.send_response(status)
                if status == 429:
                    self.send_header("retry-after", "30")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeOpenAI()

    def tearDown(self):
        self.fake.close()

    def chain(self, *models, keys="k1,k2"):
        llm = dict(CFG["llm"], timeout=5, groq_keychain_accounts=[],
                   chain=[{"provider": "groq", "model": m, "base_url": self.fake.url} for m in models])
        return providers.Chain(llm, env={"RELAY_GROQ_API_KEYS": keys})

    def test_rotates_key_on_429(self):
        self.fake.script["a"] = [(429, {"error": {"message": "rate limit"}}), (200, None)]
        c = self.chain("a")
        text, label = c.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(label, "groq/a")
        self.assertEqual([k for _, k in self.fake.seen], ["k1", "k2"])
        self.assertEqual(c.status()[0]["keys_ready"], 1)  # k1 cools for retry-after
        self.assertIn("switch_model", text)

    def test_entry_extras_reach_request_body(self):
        llm = dict(CFG["llm"], timeout=5, groq_keychain_accounts=[], chain=[
            {"provider": "groq", "model": "a", "base_url": self.fake.url, "reasoning_effort": "low"}])
        providers.Chain(llm, env={"RELAY_GROQ_API_KEY": "k"}).complete([{"role": "user", "content": "hi"}])
        self.assertEqual(self.fake.bodies[0]["reasoning_effort"], "low")
        self.assertNotIn("base_url", self.fake.bodies[0])

    def test_falls_through_on_5xx_and_missing_model(self):
        self.fake.script["gone"] = [(404, {"error": {"code": "model_not_found"}})]
        self.fake.script["down"] = [(503, {"error": "overloaded"})]
        c = self.chain("gone", "down", "ok", keys="k1")
        _, label = c.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(label, "groq/ok")
        st = {s["entry"]: s for s in c.status()}
        self.assertTrue(st["groq/gone"]["unavailable"])
        self.assertGreater(st["groq/down"]["cooling_seconds"], 0)
        self.fake.seen.clear()
        c.complete([{"role": "user", "content": "again"}])
        self.assertEqual([m for m, _ in self.fake.seen], ["ok"])  # skipped while cooling / unavailable

    def test_exhausted_when_all_keys_limited(self):
        self.fake.script["a"] = [(429, {"error": "limit"})]
        c = self.chain("a")
        with self.assertRaises(providers.ProviderError) as e:
            c.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(e.exception.kind, "exhausted")

    def test_network_error_cools_provider(self):
        llm = dict(CFG["llm"], timeout=2, chain=[{"provider": "groq", "model": "a",
                                                  "base_url": "http://127.0.0.1:9/v1"}])
        c = providers.Chain(llm, env={"RELAY_GROQ_API_KEY": "k"}, keychain=lambda s, a: None)
        with self.assertRaises(providers.ProviderError):
            c.complete([{"role": "user", "content": "hi"}])
        self.assertGreater(c.status()[0]["cooling_seconds"], 0)

    def test_key_sources(self):
        llm = {"groq_keychain_accounts": ["one", "two"], "cloudflare_account_id": "acc"}
        chain_keys = {("relay.groq", "one"): "kc1", ("relay.groq", "two"): "kc2", ("relay.cloudflare", "acc"): "cf"}
        kc = lambda s, a: chain_keys.get((s, a))  # noqa: E731
        self.assertEqual(providers.load_keys("groq", llm, {"RELAY_GROQ_API_KEYS": "e1, kc1"}, kc)[0],
                         ["e1", "kc1", "kc2"])
        self.assertEqual(providers.load_keys("cloudflare", llm, {}, kc), (["cf"], "acc"))
        c = providers.Chain(dict(llm, chain=[{"provider": "cloudflare", "model": "@cf/x"}]), env={}, keychain=kc)
        self.assertIn("/accounts/acc/ai/v1", c.entries[0].base_url)
        self.assertFalse(providers.Chain(dict(llm, chain=[{"provider": "groq", "model": "m"}]), env={},
                                         keychain=lambda s, a: None))


class AdvisorTests(ProviderTests):
    def test_parse_validates_against_catalog(self):
        ok = advisor.parse('Sure: {"action": "switch_model", "severity": "high", "model": "claude-haiku-5-5", '
                           '"effort": "max", "title": "t", "reason": "r"}', "claude", CAT)
        self.assertEqual(ok["suggested"], {"model": "claude-haiku-5-5", "effort": None})  # haiku has no efforts
        bad = advisor.parse('{"action": "switch_model", "model": "gpt-9", "effort": "high"}', "claude", CAT)
        self.assertEqual((bad["suggested"], bad["severity"]), ({"model": None, "effort": "high"}, "info"))
        self.assertIsNone(advisor.parse('{"action": "none"}', "claude", CAT))
        self.assertIsNone(advisor.parse("no json", "claude", CAT))

    def test_handle_and_rate_limits(self):
        got = []
        clock = [T0]
        a = advisor.Advisor(dict(CFG["llm"], min_interval_per_session=600, max_per_hour=2), self.chain("m"),
                            got.append, clock=lambda: clock[0])
        trigger = {"id": "t1", "rule": "mechanical", "agent": "claude", "session": "s1", "project": "p"}
        self.assertTrue(a.request("claude:s1", "claude", {"x": 1}, trigger))
        self.assertFalse(a.request("claude:s1", "claude", {"x": 1}, trigger))  # same session too soon
        self.assertTrue(a.request("claude:s2", "claude", {"x": 1}, trigger))
        self.assertFalse(a.request("claude:s3", "claude", {"x": 1}, trigger))  # hourly cap
        item = a.queue.get_nowait()
        d = a.handle(*item, cat=CAT)
        self.assertEqual((d["source"], d["trigger"], d["session"]), ("llm:groq/m", "t1", "s1"))
        self.assertEqual(got, [d])

    def test_provider_failure_means_rules_only(self):
        self.fake.script["m"] = [(500, {"error": "x"})]
        a = advisor.Advisor(CFG["llm"], self.chain("m"), lambda d: None)
        self.assertIsNone(a.handle("k", "claude", {}, None, cat=CAT))
        self.assertIn("exhausted", a.last_error)

    def test_periodic_review_keeps_session_identity(self):
        got = []
        a = advisor.Advisor(CFG["llm"], self.chain("m"), got.append)
        ctx = {"project": "p", "session_title": "Working session"}
        d = a.handle("claude:s1", "claude", ctx, None, cat=CAT)
        self.assertEqual((d["agent"], d["session"], d["project"], d["session_title"]),
                         ("claude", "s1", "p", "Working session"))
        self.assertIsNone(d["trigger"])
        self.assertEqual(d["rule"], "review")  # the app requires a rule on every advice item
        self.assertEqual(got, [d])


def tag(prompt_text="Deliver PR #83", model="Sonnet 5.5", effort="medium"):
    return "Next stage.\n<!-- relay-next " + json.dumps({"model": model, "effort": effort, "prompt": prompt_text}) + " -->"


def say(text, t=T0):
    d = assistant(9, 1000, t=t)
    d["message"]["content"] = [{"type": "text", "text": text}]
    return d


class NextSessionTests(TmpCase):
    def advice(self, lines, now=T0 + 10, agent="claude", cat=CAT):
        s = session_from(agent, lines, self.tmp)
        return s, [a for a in rules.evaluate([s], {}, CFG, cat, now) if a["rule"] == "next"]

    def test_claude_tag_becomes_advice_with_slug(self):
        s, adv = self.advice([prompt("go"), say(tag())])
        self.assertEqual(len(adv), 1)
        self.assertEqual(adv[0]["suggested"], {"model": "claude-sonnet-5-5", "effort": "medium",
                                               "prompt": "Deliver PR #83"})
        self.assertEqual(adv[0]["action"], "new_session")
        self.assertEqual(s.snapshot(T0)["next_session"]["prompt"], "Deliver PR #83")

    def test_codex_tag(self):
        text = tag(model="gpt-6-astra", effort="high")
        _, adv = self.advice([
            {"type": "session_meta", "payload": {"session_id": "c1", "cwd": "/work"}},
            {"type": "response_item", "timestamp": iso(T0), "payload": {
                "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}}],
            agent="codex")
        self.assertEqual(adv[0]["suggested"]["model"], "gpt-6-astra")
        self.assertEqual(adv[0]["suggested"]["effort"], "high")

    def test_bad_json_ignored_and_last_tag_wins(self):
        _, adv = self.advice([prompt("go"), say("<!-- relay-next {oops} -->")])
        self.assertEqual(adv, [])
        _, adv = self.advice([prompt("go"), say(tag("first") + "\n" + tag("second"))])
        self.assertEqual(adv[0]["suggested"]["prompt"], "second")
        self.assertIsNone(live.parse_next('<!-- relay-next {"model": "x"} -->'))

    def test_stale_after_user_prompt_or_12h(self):
        _, adv = self.advice([prompt("go"), say(tag()), prompt("one more thing")])
        self.assertEqual(adv, [])
        _, adv = self.advice([prompt("go"), say(tag())], now=T0 + 13 * 3600)
        self.assertEqual(adv, [])

    def test_effort_dropped_when_model_lists_none(self):
        _, adv = self.advice([prompt("go"), say(tag(model="haiku", effort="low"))])
        self.assertEqual(adv[0]["suggested"]["model"], "claude-haiku-5-5")
        self.assertIsNone(adv[0]["suggested"]["effort"])

    def test_unknown_model_kept_raw(self):
        _, adv = self.advice([prompt("go"), say(tag(model="mystery-1"))])
        self.assertEqual(adv[0]["suggested"]["model"], "mystery-1")

    def test_same_tag_same_key_across_reread(self):
        lines = [prompt("go"), say(tag())]
        _, a = self.advice(lines)
        _, b = self.advice(lines)
        self.assertEqual(a[0]["key"], b[0]["key"])
        log = store.AdviceLog(None)
        self.assertIsNotNone(log.add(a[0]))
        self.assertIsNone(log.add(b[0]))

    def test_notifier_fires_for_next(self):
        sent = []
        n = notify.Notifier(True, 3600, send=lambda *a: sent.append(a), clock=lambda: T0)
        _, adv = self.advice([prompt("go"), say(tag())])
        self.assertTrue(n.maybe(adv[0]))
        self.assertEqual(sent[0][0], "relay: next session ready")
        self.assertIn("claude-sonnet-5-5 medium", sent[0][2])


class StoreNotifyTests(TmpCase):
    def test_log_dedupes_and_keeps_outcomes(self):
        path = os.path.join(self.tmp, "advice.jsonl")
        log = store.AdviceLog(path)
        d = log.add({"key": "k1", "rule": "r", "severity": "warn", "title": "t", "reason": "x"})
        self.assertIsNone(log.add({"key": "k1"}))
        self.assertEqual(log.set_outcome(d["id"], "wrong", "not now")["outcome"], "wrong")
        with self.assertRaises(ValueError):
            log.set_outcome(d["id"], "meh")
        again = store.AdviceLog(path)
        self.assertEqual(again.list()[0]["outcome"], "wrong")
        self.assertTrue(again.seen("k1"))

    def test_notifier_only_high_and_deduped(self):
        sent, clock = [], [T0]
        n = notify.Notifier(True, 3600, send=lambda *a: sent.append(a), clock=lambda: clock[0])
        a = {"severity": "high", "agent": "claude", "session": "s", "rule": "repeat", "title": "T", "reason": "R"}
        self.assertTrue(n.maybe(a))
        self.assertFalse(n.maybe(dict(a)))
        self.assertFalse(n.maybe(dict(a, severity="warn", rule="other")))
        clock[0] += 3601
        self.assertTrue(n.maybe(a))
        self.assertEqual(len(sent), 2)


class ApiTests(TmpCase):
    def setUp(self):
        super().setUp()
        path = os.path.join(self.tmp, "p", "s1.jsonl")
        os.makedirs(os.path.dirname(path))
        write_lines(path, [assistant(1, 320_000, t=time.time())])
        env = mock.patch.dict(os.environ, {"RELAY_STATE_DIR": self.tmp})
        env.start()
        self.addCleanup(env.stop)
        tailer = live.Tailer(discover=lambda agent, since: [path] if agent == "claude" else [])
        self.sent = []
        self.engine = watch.Engine(CFG, use_llm=False, tailer=tailer, out=io.StringIO(),
                                   notifier=notify.Notifier(True, send=lambda *a: self.sent.append(a)))
        self.httpd = server.serve(self.engine, 0)
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.engine.stopping.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers or {})
        r = c.getresponse()
        data = json.loads(r.read() or b"null")
        c.close()
        return r.status, data

    def test_sessions_advice_outcome(self):
        with mock.patch.object(watch, "read_codex_quota", return_value=None):
            emitted = self.engine.tick()
        self.assertEqual([(d["rule"], d["severity"]) for d in emitted], [("context", "high")])
        self.assertEqual(len(self.sent), 1)  # high severity → one notification
        self.assertEqual(self.engine.tick(time.time() + 1), [])  # same key is not emitted twice
        status, sessions = self.req("GET", "/api/sessions")
        self.assertEqual((status, sessions[0]["context"]), (200, 320_000))
        status, advice = self.req("GET", "/api/advice")
        aid = advice[0]["id"]
        status, d = self.req("POST", f"/api/advice/{aid}/outcome", {"outcome": "useful"},
                             {"Content-Type": "application/json"})
        self.assertEqual((status, d["outcome"]), (200, "useful"))
        self.assertEqual(self.req("POST", f"/api/advice/{aid}/outcome", {"outcome": "useful"})[0], 415)
        self.assertEqual(self.req("GET", "/api/health", headers={"Host": "evil.test"})[0], 403)
        with open(os.path.join(self.tmp, "advice.jsonl")) as f:
            self.assertEqual(json.loads(f.read().splitlines()[-1])["outcome"], "useful")

    def test_config_round_trip_and_reload(self):
        home = os.path.join(self.tmp, "home")
        with mock.patch.dict(os.environ, {"HOME": home}):
            json_h = {"Content-Type": "application/json"}
            status, d = self.req("POST", "/api/config", {"soft_limit": 120_000, "llm": {"send": {"prompt": False}}},
                                 json_h)
            self.assertEqual((status, d["effective"]["soft_limit"]), (200, 120_000))
            self.assertEqual(self.engine.cfg["llm"]["send"], {"prompt": False})
            self.assertEqual(self.engine.cfg["llm"]["max_per_hour"], 20)  # other llm keys keep defaults
            with open(os.path.join(home, ".config", "relay", "config.json")) as f:
                self.assertEqual(json.load(f)["soft_limit"], 120_000)
            self.assertEqual(self.req("POST", "/api/config", {"soft_limit": "big"}, json_h)[0], 400)
            self.assertEqual(self.req("POST", "/api/config", {"nope": 1}, json_h)[0], 400)
            status, d = self.req("POST", "/api/config", {"soft_limit": None}, json_h)
            self.assertEqual(d["effective"]["soft_limit"], 150_000)
            self.assertNotIn("soft_limit", d["user"])
            status, agents = self.req("GET", "/api/agents")
            self.assertEqual([(a["agent"], a["hooks_installed"]) for a in agents], [("claude", False), ("codex", False)])

    def test_quota_endpoint_reports_state(self):
        status = {"statusline": False}
        with mock.patch.object(watch, "read_codex_quota", return_value=None), \
                mock.patch.object(watch.install, "status", side_effect=lambda agent: status):
            self.engine.tick()
            self.assertEqual(self.req("GET", "/api/quota")[1]["status"]["claude"],
                             {"state": "no_source", "source": "statusline"})
            status["statusline"] = True  # installed, but only the desktop app ran: nothing recorded
            self.engine.tick(time.time() + watch.SCAN_SECONDS + 1)
            self.assertEqual(self.req("GET", "/api/quota")[1]["status"]["claude"]["state"], "no_reading")
            with open(os.path.join(self.tmp, "quota.jsonl"), "w") as f:
                f.write(json.dumps({"seven_day": {"used_percentage": 30, "resets_at": time.time() + 3600}}) + "\n")
            self.engine.tick(time.time() + 2 * watch.SCAN_SECONDS + 2)
        q = self.req("GET", "/api/quota")[1]
        self.assertEqual((q["claude"]["seven_day"]["used_percentage"], q["status"]["claude"]["state"]), (30, "ok"))
        self.assertEqual(q["status"]["codex"]["state"], "no_reading")

    def test_usage_endpoint_and_context_history(self):
        with mock.patch.object(watch.usage, "data", return_value={"agents": {}}) as data:
            self.assertEqual(self.req("GET", "/api/usage?days=3")[1], {"agents": {}, "days": 3})
            self.req("GET", "/api/usage?days=3")
            self.assertEqual(data.call_count, 1)  # cached
        with mock.patch.object(watch, "read_codex_quota", return_value=None):
            self.engine.tick()
        self.assertEqual(self.req("GET", "/api/sessions")[1][0]["context_history"], [320_000])

    def test_advisor_data_toggles(self):
        with mock.patch.object(watch, "read_codex_quota", return_value=None):
            self.engine.tick()
        s = next(iter(self.engine.tailer.sessions.values()))
        self.assertIn("last_user_prompt", self.engine.llm_context(s, None))
        self.engine.cfg = dict(CFG, llm=dict(CFG["llm"], send={"prompt": False, "files": False}))
        ctx = self.engine.llm_context(s, None)
        self.assertNotIn("last_user_prompt", ctx)
        self.assertNotIn("recent_files", ctx)
        self.assertIn("recent_errors", ctx)

    def test_sse_stream(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", "/api/events")
        r = c.getresponse()
        self.assertEqual(r.getheader("Content-Type"), "text/event-stream")
        self.assertEqual(r.fp.readline(), b": connected\n")
        r.fp.readline()
        self.engine.hub.publish("advice", {"id": "x1", "title": "hello"})
        self.assertEqual(r.fp.readline(), b"event: advice\n")
        self.assertEqual(json.loads(r.fp.readline()[6:]), {"id": "x1", "title": "hello"})
        c.close()


class ConfigTests(TmpCase):
    def test_nested_sections_merge(self):
        os.makedirs(os.path.join(self.tmp, ".relay"))
        with open(os.path.join(self.tmp, ".relay", "config.json"), "w") as f:
            json.dump({"llm": {"max_per_hour": 3}, "cache_ttl": {"claude": 300}}, f)
        cfg = config.load(self.tmp)
        self.assertEqual((cfg["llm"]["max_per_hour"], cfg["llm"]["enabled"]), (3, True))
        self.assertEqual(cfg["cache_ttl"], {"claude": 300, "codex": 600})
        self.assertEqual(config.DEFAULTS["cache_ttl"]["claude"], 3600)


if __name__ == "__main__":
    unittest.main()
