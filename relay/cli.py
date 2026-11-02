import argparse
import datetime
import json

from relay import __version__, config, guard, install, statusline, transcripts, usage


def main(argv=None):
    p = argparse.ArgumentParser(prog="relay", description="Keep AI coding agents from burning quota "
                                "on oversized contexts.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("hook", help="hook handler (reads the agent's hook JSON on stdin)")
    h.add_argument("agent", choices=transcripts.AGENTS)

    u = sub.add_parser("usage", help="where the tokens went")
    u.add_argument("--days", type=int, default=30)
    u.add_argument("--since", type=datetime.date.fromisoformat)
    u.add_argument("--agent", choices=transcripts.AGENTS, action="append")
    u.add_argument("--cap", type=int, help="context cap for the what-if line (default: soft_limit)")
    u.add_argument("--top", type=int, default=10)
    u.add_argument("--json", action="store_true")

    c = sub.add_parser("context", help="current context size of a transcript")
    c.add_argument("agent", choices=transcripts.AGENTS)
    c.add_argument("transcript")

    sub.add_parser("statusline", help="Claude Code status line (reads its JSON on stdin)")
    sub.add_parser("config", help="show the effective settings for the current directory")

    i = sub.add_parser("install", help="add the guard hooks and the handoff skill to an agent")
    i.add_argument("agent", choices=transcripts.AGENTS)
    i.add_argument("--autocompact", type=int, help="Claude Code: auto-compact window in tokens")
    i.add_argument("--no-statusline", action="store_true")

    r = sub.add_parser("uninstall", help="remove relay from an agent")
    r.add_argument("agent", choices=transcripts.AGENTS)
    r.add_argument("--autocompact", action="store_true", help="also drop autoCompactWindow")

    w = sub.add_parser("watch", help="advise on live sessions (rules + LLM), serve the local API")
    w.add_argument("--port", type=int, help="API port (default: api_port, 8765)")
    w.add_argument("--no-llm", action="store_true", help="rules only")
    w.add_argument("--no-notify", action="store_true", help="no macOS notifications")
    w.add_argument("--once", action="store_true", help="one pass over current sessions, print, exit")
    w.add_argument("--json", action="store_true", help="with --once: JSON output")

    v = sub.add_parser("advisor", help="LLM advisor tools")
    v.add_argument("what", choices=["check"], help="check: send one tiny request to every chain entry")

    a = p.parse_args(argv)
    if a.cmd == "watch":
        from relay import watch
        cfg = config.load()
        if a.once:
            return watch.once(cfg, a.json)
        return watch.Engine(cfg, use_llm=not a.no_llm, use_notify=not a.no_notify).run(a.port)
    if a.cmd == "advisor":
        from relay import providers
        return providers.check(config.load().get("llm") or {})
    if a.cmd == "hook":
        return guard.run_hook(a.agent)
    if a.cmd == "usage":
        since = a.since or datetime.date.today() - datetime.timedelta(days=a.days)
        cap = a.cap or int(config.load()["soft_limit"])
        return usage.report(tuple(a.agent or transcripts.AGENTS), since, cap, a.top, a.json)
    if a.cmd == "context":
        ctx = transcripts.current_context(a.agent, a.transcript)
        print(ctx if ctx is not None else "unknown")
        return 0
    if a.cmd == "statusline":
        return statusline.run()
    if a.cmd == "config":
        print(json.dumps(config.load(), indent=2))
        return 0
    if a.cmd == "install":
        install.install(a.agent, a.autocompact, not a.no_statusline)
        return 0
    if a.cmd == "uninstall":
        install.uninstall(a.agent, a.autocompact)
        return 0
    return 2
