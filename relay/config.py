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
    cfg = dict(DEFAULTS)
    cfg.update(_read_json(home(".config", "relay", "config.json")))
    root = project_root(cwd)
    cfg.update(_read_json(os.path.join(root, ".relay", "config.json")))
    cfg["project_root"] = root
    return cfg
