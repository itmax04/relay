"""Models the owner can switch to, with a coarse tier (strong / standard / light) and effort levels.

Codex: ~/.codex/models_cache.json, written by the Codex CLI itself; tiers are guessed from the
model descriptions. Claude: no local catalog exists, so a dated built-in list is used, and
~/.config/relay/claude_models.json replaces it when present (same shape as CLAUDE_MODELS).
"""
import json
import os
import re

from relay import config

CLAUDE_MODELS = {
    "as_of": "2026-11-02",
    "source": "models seen in this Mac's Claude Code transcripts and the Claude Code model list",
    "models": [
        {"slug": "claude-opus-5-5", "name": "Opus 5.5", "alias": "opus", "tier": "strong",
         "efforts": ["low", "medium", "high", "xhigh", "max"]},
        {"slug": "claude-sonnet-5-5", "name": "Sonnet 5.5", "alias": "sonnet", "tier": "standard",
         "efforts": ["low", "medium", "high", "xhigh", "max"]},
        {"slug": "claude-haiku-5-5", "name": "Haiku 5.5", "alias": "haiku", "tier": "light", "efforts": []},
    ],
}

TIERS = ("light", "standard", "strong")

# Model and effort per stage tier for the stage rule that `relay install` writes. Codex lists two
# sets: the first works with the installed CLI, the second once /model offers those models.
STAGE_TIERS = {
    "claude": [{"strong": ("claude-opus-5-5", "high"), "standard": ("claude-sonnet-5-5", "medium"),
                "light": ("claude-haiku-5-5", "low")}],
    "codex": [{"strong": ("gpt-5.6-sol", "high"), "standard": ("gpt-5.6-terra", "medium"),
               "light": ("gpt-5.6-luna", "low")},
              {"when": "if /model lists gpt-6", "strong": ("gpt-6-astra", "high"),
               "standard": ("gpt-6.1-sol", "medium"), "light": ("gpt-6-luna", "low")}],
}
_STRONG = re.compile(r"frontier|most demanding|most capable", re.I)
_LIGHT = re.compile(r"fast and (affordable|efficient)|easier tasks|lightweight", re.I)


def _codex_tier(description):
    if _STRONG.search(description or ""):
        return "strong"
    if _LIGHT.search(description or ""):
        return "light"
    return "standard"


def codex_models(path=None):
    path = path or config.home(".codex", "models_cache.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for m in data.get("models") or []:
        if not isinstance(m, dict) or m.get("visibility") not in (None, "list") or not m.get("slug"):
            continue
        out.append({"slug": m["slug"], "name": m.get("display_name") or m["slug"],
                    "tier": _codex_tier(m.get("description")), "description": m.get("description") or "",
                    "efforts": [e.get("effort") for e in m.get("supported_reasoning_levels") or []
                                if isinstance(e, dict) and e.get("effort")],
                    "default_effort": m.get("default_reasoning_level")})
    return out


def claude_models(path=None):
    path = path or config.home(".config", "relay", "claude_models.json")
    data = CLAUDE_MODELS
    try:
        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict) and isinstance(loaded.get("models"), list):
            data = loaded
    except (OSError, ValueError):
        pass
    return [dict(m, name=m.get("name") or m.get("alias") or m["slug"]) for m in data["models"] if m.get("slug")]


def stage_tiers(agent):
    """One line naming the model and effort per tier, e.g. "strong = Opus 5.5 high; ..."."""
    names = {m["slug"]: m["name"] for m in claude_models()}
    parts = []
    for i, tiers in enumerate(STAGE_TIERS[agent]):
        line = "; ".join(f"{t} = {names.get(tiers[t][0], tiers[t][0])} {tiers[t][1]}"
                         for t in reversed(TIERS))
        parts.append(line if i == 0 else f"({tiers['when']}: {line})")
    return " ".join(parts)


def load(cfg=None):
    """{agent: [model, ...]} with config "model_tiers" ({slug: tier}) applied on top."""
    overrides = (cfg or {}).get("model_tiers") or {}
    cat = {"claude": claude_models(), "codex": codex_models()}
    for models in cat.values():
        for m in models:
            if overrides.get(m["slug"]) in TIERS:
                m["tier"] = overrides[m["slug"]]
    return cat


def find(cat, agent, slug):
    for m in cat.get(agent) or []:
        if m["slug"] == slug or (slug or "").startswith(m["slug"]):
            return m
    return None


def cheaper(cat, agent, slug):
    """The best model one tier below `slug`, or None."""
    cur = find(cat, agent, slug)
    if not cur or cur["tier"] == "light":
        return None
    want = TIERS[TIERS.index(cur["tier"]) - 1]
    for m in cat.get(agent) or []:
        if m["tier"] == want:
            return m
    return None
