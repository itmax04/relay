import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from relay import config, guard, install, statusline, transcripts  # noqa: E402


def claude_line(msg_id, ctx, sidechain=False, out=10):
    return json.dumps({"type": "assistant", "sessionId": "s1", "requestId": "r" + msg_id,
                       "isSidechain": sidechain, "timestamp": "2026-11-01T10:00:00Z",
                       "message": {"id": msg_id, "model": "claude-opus-5-5", "usage": {
                           "input_tokens": 5, "cache_creation_input_tokens": 100,
                           "cache_read_input_tokens": ctx - 105, "output_tokens": out}}})


def codex_lines(*contexts):
    yield json.dumps({"type": "session_meta", "payload": {"session_id": "c1", "cwd": "/work"}})
    yield json.dumps({"type": "turn_context", "payload": {"model": "gpt-6.1-sol"}})
    total = 0
    for ctx in contexts:
        total += ctx
        yield json.dumps({"type": "event_msg", "timestamp": "2026-11-01T10:00:00Z", "payload": {
            "type": "token_count", "rate_limits": {"secondary": {"used_percent": 55.0}},
            "info": {"total_token_usage": {"total_tokens": total},
                     "last_token_usage": {"input_tokens": ctx, "cached_input_tokens": ctx - 50,
                                          "output_tokens": 7}}}})


def read_json(path):
    with open(path) as f:
        return json.load(f)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        patcher = mock.patch.dict(os.environ, {"HOME": self.home, "RELAY_STATE_DIR": os.path.join(self.home, "state")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, lines):
        path = os.path.join(self.home, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        return path


class TranscriptTests(Base):
    def test_claude_context_is_last_main_thread_call(self):
        path = self.write("c.jsonl", [claude_line("a", 120_000), claude_line("a", 120_000),
                                      claude_line("b", 160_000), claude_line("x", 900_000, sidechain=True)])
        self.assertEqual(transcripts.current_context("claude", path), 160_000)
        calls, _ = transcripts.read_calls("claude", path)
        self.assertEqual(len(calls), 3)  # the repeated streaming line is counted once

    def test_codex_context_and_rate_limits(self):
        path = self.write("r.jsonl", list(codex_lines(30_000, 210_000)))
        self.assertEqual(transcripts.current_context("codex", path), 210_000)
        calls, reader = transcripts.read_calls("codex", path)
        self.assertEqual([c.cache_read for c in calls], [29_950, 209_950])
        self.assertEqual(calls[0].model, "gpt-6.1-sol")
        self.assertEqual(reader.rate_limits["secondary"]["used_percent"], 55.0)

    def test_tail_read_skips_partial_first_line(self):
        filler = json.dumps({"type": "user", "pad": "x" * (2 << 20)})
        path = self.write("big.jsonl", [claude_line("a", 50_000), filler, claude_line("b", 70_000)])
        self.assertEqual(transcripts.current_context("claude", path), 70_000)
        path = self.write("big2.jsonl", [claude_line("a", 50_000), filler])
        self.assertEqual(transcripts.current_context("claude", path), 50_000)

    def test_missing_or_unknown(self):
        self.assertIsNone(transcripts.current_context("claude", "/nope.jsonl"))
        self.assertIsNone(transcripts.current_context("claude", self.write("e.jsonl", ["{}"])))


class GuardTests(Base):
    def run_hook(self, agent, payload):
        out = io.StringIO()
        guard.run_hook(agent, io.StringIO(json.dumps(payload)), out)
        return json.loads(out.getvalue()) if out.getvalue() else None

    def payload(self, event, ctx, **extra):
        path = self.write("t.jsonl", [claude_line("m%d" % ctx, ctx)])
        return dict({"hook_event_name": event, "session_id": "s1", "transcript_path": path,
                     "cwd": self.home}, **extra)

    def test_quiet_below_soft_limit(self):
        self.assertIsNone(self.run_hook("claude", self.payload("UserPromptSubmit", 90_000)))
        self.assertIsNone(self.run_hook("claude", self.payload("Stop", 90_000)))

    def test_prompt_over_soft_and_hard(self):
        out = self.run_hook("claude", self.payload("UserPromptSubmit", 180_000))
        self.assertIn("180K", out["systemMessage"])
        self.assertIn("soft limit", out["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit")
        out = self.run_hook("claude", self.payload("UserPromptSubmit", 320_000))
        self.assertIn("hard limit", out["hookSpecificOutput"]["additionalContext"])

    def test_stop_blocks_once_per_level(self):
        out = self.run_hook("claude", self.payload("Stop", 180_000))
        self.assertEqual(out["decision"], "block")
        self.assertIn("relay-handoff", out["reason"])
        self.assertIsNone(self.run_hook("claude", self.payload("Stop", 190_000)))
        self.assertEqual(self.run_hook("claude", self.payload("Stop", 310_000))["decision"], "block")
        self.assertIsNone(self.run_hook("claude", self.payload("Stop", 330_000)))

    def test_stop_respects_stop_hook_active(self):
        self.assertIsNone(self.run_hook("claude", self.payload("Stop", 180_000, stop_hook_active=True)))

    def test_compact_resets_stop_state(self):
        self.run_hook("claude", self.payload("Stop", 180_000))
        self.run_hook("claude", self.payload("SessionStart", 20_000, source="compact"))
        self.assertEqual(self.run_hook("claude", self.payload("Stop", 170_000))["decision"], "block")

    def test_session_start_points_to_handoff_and_warns_on_resume(self):
        os.makedirs(os.path.join(self.home, ".relay"))
        with open(os.path.join(self.home, ".relay", "handoff.md"), "w") as f:
            f.write("state")
        out = self.run_hook("claude", self.payload("SessionStart", 10, source="startup"))
        self.assertIn(".relay/handoff.md", out["hookSpecificOutput"]["additionalContext"])
        self.assertNotIn("systemMessage", out)
        out = self.run_hook("claude", self.payload("SessionStart", 10, source="resume", context_tokens=400_000))
        self.assertIn("400K", out["systemMessage"])

    def test_project_config_overrides_limits(self):
        os.makedirs(os.path.join(self.home, ".relay"))
        with open(os.path.join(self.home, ".relay", "config.json"), "w") as f:
            json.dump({"soft_limit": 50_000}, f)
        self.assertIsNotNone(self.run_hook("claude", self.payload("UserPromptSubmit", 60_000)))

    def test_codex_payload(self):
        path = self.write("r.jsonl", list(codex_lines(200_000)))
        out = self.run_hook("codex", {"hook_event_name": "Stop", "session_id": "c1",
                                      "transcript_path": path, "cwd": self.home, "stop_hook_active": False})
        self.assertEqual(out["decision"], "block")

    def test_bad_input_is_silent(self):
        out = io.StringIO()
        self.assertEqual(guard.run_hook("claude", io.StringIO("not json"), out), 0)
        self.assertEqual(out.getvalue(), "")
        self.assertTrue(os.path.exists(os.path.join(config.state_dir(), "errors.log")))


class InstallTests(Base):
    def test_install_is_idempotent_and_keeps_foreign_hooks(self):
        settings = os.path.join(self.home, ".claude", "settings.json")
        os.makedirs(os.path.dirname(settings))
        with open(settings, "w") as f:
            json.dump({"permissions": {"defaultMode": "default"},
                       "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other"}]}]}}, f)
        install.install("claude", autocompact=350_000)
        install.install("claude", autocompact=350_000)
        data = read_json(settings)
        self.assertEqual(data["permissions"], {"defaultMode": "default"})
        self.assertEqual(data["autoCompactWindow"], 350_000)
        stop = [h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(len(stop), 2)
        self.assertIn("other", stop)
        self.assertIn("statusline", data["statusLine"]["command"])
        self.assertTrue(os.path.islink(os.path.join(self.home, ".claude", "skills", "relay-handoff")))
        install.uninstall("claude", drop_autocompact=True)
        data = read_json(settings)
        self.assertEqual(data["hooks"], {"Stop": [{"hooks": [{"type": "command", "command": "other"}]}]})
        self.assertNotIn("statusLine", data)
        self.assertNotIn("autoCompactWindow", data)

    def test_codex_install_writes_hooks_json(self):
        install.install("codex")
        data = read_json(os.path.join(self.home, ".codex", "hooks.json"))
        self.assertEqual(set(data["hooks"]), set(install.EVENTS))
        self.assertTrue(os.path.islink(os.path.join(self.home, ".agents", "skills", "relay-handoff")))
        self.assertTrue(os.path.islink(os.path.join(self.home, ".agents", "skills", "relay-plan")))
        with open(os.path.join(self.home, ".codex", "AGENTS.md")) as f:
            self.assertIn("standard = gpt-5.6-terra medium", f.read())

    def test_stage_rule_is_one_block_and_leaves_other_text(self):
        path = os.path.join(self.home, ".claude", "CLAUDE.md")
        os.makedirs(os.path.dirname(path))
        original = "# Mine\n\nKeep this.\n"
        with open(path, "w") as f:
            f.write(original)
        install.install("claude")
        install.install("claude")
        with open(path) as f:
            text = f.read()
        self.assertTrue(text.startswith(original))
        self.assertEqual(text.count(install.RULE_BEGIN), 1)
        self.assertIn("Opus 5.5 high", text)
        self.assertTrue(os.path.islink(os.path.join(self.home, ".claude", "skills", "relay-plan")))
        st = install.status("claude")
        self.assertTrue(st["rule_installed"] and st["plan_skill_linked"] and st["skill_linked"])
        install.uninstall("claude")
        with open(path) as f:
            self.assertEqual(f.read(), original)
        self.assertFalse(os.path.lexists(os.path.join(self.home, ".claude", "skills", "relay-plan")))
        self.assertFalse(install.status("claude")["rule_installed"])

    def test_stage_rule_is_updated_in_place(self):
        path = os.path.join(self.home, ".codex", "AGENTS.md")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            f.write(f"Before.\n\n{install.RULE_BEGIN}\nold rule\n{install.RULE_END}\n\nAfter.\n")
        install.install("codex")
        with open(path) as f:
            text = f.read()
        self.assertEqual(text, f"Before.\n\n{install.rule_block('codex')}\n\nAfter.\n")
        install.uninstall("codex")
        with open(path) as f:
            self.assertEqual(f.read(), "Before.\n\nAfter.\n")

    def test_stage_rule_skips_broken_markers_and_can_be_left_out(self):
        path = os.path.join(self.home, ".claude", "CLAUDE.md")
        install.install("claude", rule=False)
        self.assertFalse(os.path.exists(path))
        with open(path, "w") as f:
            f.write(f"{install.RULE_BEGIN}\nno end marker\n")
        install.install("claude")
        with open(path) as f:
            self.assertEqual(f.read(), f"{install.RULE_BEGIN}\nno end marker\n")


class StatuslineTests(Base):
    def test_line_and_quota_log(self):
        data = {"session_id": "s1", "cwd": self.home, "model": {"display_name": "Opus"},
                "context_window": {"current_usage": {"input_tokens": 1000, "cache_read_input_tokens": 170_000}},
                "rate_limits": {"five_hour": {"used_percentage": 4}, "seven_day": {"used_percentage": 76}}}
        for _ in range(2):
            out = io.StringIO()
            statusline.run(io.StringIO(json.dumps(data)), out)
        self.assertEqual(out.getvalue(), "ctx 171K ! · 5h 4% · week 76% · Opus")
        with open(os.path.join(config.state_dir(), "quota.jsonl")) as f:
            self.assertEqual(len(f.read().splitlines()), 1)  # unchanged readings are not repeated


if __name__ == "__main__":
    unittest.main()
