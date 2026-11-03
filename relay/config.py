"""Settings: built-in defaults, then ~/.config/relay/config.json, then <project>/.relay/config.json."""
import json
import os

DEFAULTS = {
    # Context (tokens re-sent with every call) at which the guard asks for a handoff.
    "soft_limit": 150_000,
    # Context at which the guard tells the agent to stop starting new work in this session.
    "hard_limit": 300_000,
    # Where the handoff lives, relative to the project root. A project whose own process
    # already defines a handoff document should point this at it.
    "handoff_path": ".relay/handoff.md",

    # `relay watch`: transcripts modified within this many hours count as active sessions.
    "watch_active_hours": 12,
    # Prompt-cache lifetime after the last call, seconds. Claude Code subscriptions use 1 h
    # (5 min in usage overage); OpenAI keeps cached prefixes for minutes, not hours.
    "cache_ttl": {"claude": 3600, "codex": 600},
    "cache_warn_seconds": 300,
    # Context at which cache expiry starts to matter.
    "big_context": 100_000,
    "repeat_failures": 3,
    "large_read_tokens": 20_000,
    "mechanical_run": 10,
    "quota_warn": 75,
    "quota_high": 90,
    "api_port": 8765,
    "notify": True,
    # Optional {model slug: "light" | "standard" | "strong"} to correct guessed tiers.
    "model_tiers": {},
    # `relay run` (the dispatcher, docs/design/dispatcher.md).
    "run": {
        # Agents in order of preference. v1 runs Codex only.
        "agents": ["codex"],
        # Codex CLI to run. The Codex CLI 0.144.6 on PATH (checked 2026-11-03) accepts only the
        # gpt-5.6 models with a ChatGPT login; gpt-6* need a newer Codex.
        "codex_bin": "codex",
        # tier -> model + effort, per agent.
        "tiers": {
            "codex": {"strong": {"model": "gpt-5.6-sol", "effort": "xhigh"},
                      "standard": {"model": "gpt-5.6-sol", "effort": "medium"},
                      "light": {"model": "gpt-5.6-luna", "effort": "low"}},
            "claude": {"strong": {"model": "claude-opus-5-5", "effort": "high"},
                       "standard": {"model": "claude-sonnet-5-5", "effort": "medium"},
                       "light": {"model": "claude-haiku-5-5", "effort": None}},
        },
        "max_retries": 1,        # per tier, after the first attempt; then the tier goes up
        "max_attempts": 30,      # agent attempts in the whole run (a quota wait is not counted) → stopped
        "max_steps": 20,
        "max_minutes": 240,      # whole run, waiting for the owner or quota excluded
        "step_timeout": 1800,    # seconds per agent process
        "check_timeout": 600,
        "setup_timeout": 1800,   # seconds per Setup: command of the plan
        "min_free_gb": 5,        # stop before a worktree or an attempt when the disk has less free
        "cleanup": "on_done",    # "on_done": a finished run removes its worktree (the branch stays); "never"
        "caffeinate": True,      # keep the Mac awake (/usr/bin/caffeinate) while the dispatcher runs
        "preflight": True,       # `relay run start` checks codex, models, plan and disk first
        # Wait for the window to reset when the 5-hour window is above this, or the week above week_stop.
        "quota_switch_at": 80,
        "quota_week_stop": 98,
    },
    "llm": {
        "enabled": True,
        # Tried in order; a model the account cannot use is skipped for a day.
        # A free Groq org sees only these chat models (checked 2026-11-03). Lingo uses the same three;
        # Groq limits are per organization and model, so a key from another org keeps them apart.
        "chain": [
            # Extra keys of an entry go into the request body; low reasoning leaves room for the answer.
            {"provider": "groq", "model": "openai/gpt-oss-120b", "reasoning_effort": "low"},
            {"provider": "groq", "model": "openai/gpt-oss-20b", "reasoning_effort": "low"},
            {"provider": "groq", "model": "qwen/qwen3.8-27b"},
            {"provider": "cloudflare", "model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast"},
        ],
        # Keychain accounts under service "relay.groq", one per Groq key (see README).
        "groq_keychain_accounts": ["default"],
        "cloudflare_account_id": None,
        "timeout": 25,
        "provider_cooldown": 300,
        "min_interval_per_session": 600,
        "max_per_hour": 20,
        # A session gets an LLM review every this many model calls while its context is big.
        "review_every_calls": 40,
        # What the advisor model may see besides metrics: the last prompt text, error excerpts,
        # file names and tool-call labels. Missing keys count as true.
        "send": {"prompt": True, "errors": True, "files": True, "tools": True},
    },
}


def home(*parts):
    return os.path.join(os.path.expanduser("~"), *parts)


def state_dir():
    return os.environ.get("RELAY_STATE_DIR") or home(".local", "state", "relay")


def project_root(cwd):
    """Nearest ancestor of cwd holding .git or .relay; cwd itself when there is none."""
    path = os.path.abspath(cwd or os.getcwd())
    probe = path
    while True:
        if os.path.exists(os.path.join(probe, ".git")) or os.path.isdir(os.path.join(probe, ".relay")):
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:
            return path
        probe = parent


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def user_path():
    return home(".config", "relay", "config.json")


def _check(key, value, default):
    if value is None or default is None:
        return
    if isinstance(default, bool):
        ok = isinstance(value, bool)
    elif isinstance(default, int):
        ok = isinstance(value, int) and not isinstance(value, bool)
    else:
        ok = isinstance(value, type(default))
    if not ok:
        raise ValueError(f"{key}: expected {type(default).__name__}")


def save_user(patch):
    """Merge `patch` into the user config file (sections merge one level; None resets a key)."""
    if not isinstance(patch, dict):
        raise ValueError("send a JSON object")
    path = user_path()
    data = _read_json(path)
    for k, v in patch.items():
        if k not in DEFAULTS:
            raise ValueError(f"unknown setting: {k}")
        default = DEFAULTS[k]
        if isinstance(default, dict) and isinstance(v, dict) and k != "model_tiers":
            section = dict(data.get(k) or {})
            for sk, sv in v.items():
                _check(f"{k}.{sk}", sv, default.get(sk))
                if sv is None:
                    section.pop(sk, None)
                else:
                    section[sk] = sv
            data[k] = section
        elif v is None:
            data.pop(k, None)
        else:
            _check(k, v, default)
            data[k] = v
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)
    return data


def load_user():
    return _read_json(user_path())


def load(cwd=None):
    cfg = {k: dict(v) if isinstance(v, dict) else v for k, v in DEFAULTS.items()}
    root = project_root(cwd)
    for path in (user_path(), os.path.join(root, ".relay", "config.json")):
        for k, v in _read_json(path).items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)  # nested sections merge, so a file can override one setting
            else:
                cfg[k] = v
    cfg["project_root"] = root
    return cfg
