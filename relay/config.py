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
    "llm": {
        "enabled": True,
        # Tried in order; a model the account cannot use is skipped for a day.
        # Lingo uses Groq openai/gpt-oss-120b, openai/gpt-oss-20b and qwen/qwen3.8-27b, and
        # Workers AI @cf/openai/gpt-oss-120b / -20b; Groq limits are per organization and model.
        "chain": [
            {"provider": "groq", "model": "llama-3.3-70b-versatile"},
            {"provider": "groq", "model": "minimaxai/minimax-m2.7"},
            {"provider": "groq", "model": "llama-3.1-8b-instant"},
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


def load(cwd=None):
    cfg = {k: dict(v) if isinstance(v, dict) else v for k, v in DEFAULTS.items()}
    root = project_root(cwd)
    for path in (home(".config", "relay", "config.json"), os.path.join(root, ".relay", "config.json")):
        for k, v in _read_json(path).items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)  # nested sections merge, so a file can override one setting
            else:
                cfg[k] = v
    cfg["project_root"] = root
    return cfg
