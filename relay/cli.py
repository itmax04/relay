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

    i = sub.add_parser("install", help="add the guard hooks, the skills and the stage rule to an agent")
    i.add_argument("agent", choices=transcripts.AGENTS)
    i.add_argument("--autocompact", type=int, help="Claude Code: auto-compact window in tokens")
    i.add_argument("--no-statusline", action="store_true")
    i.add_argument("--no-rule", action="store_true", help="skip the stage rule in CLAUDE.md / AGENTS.md")

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

    pl = sub.add_parser("plan", help="draft .relay/plan.md for `relay run` with a strong model")
    pl.add_argument("task", help="what the run should achieve, in a few sentences")
    pl.add_argument("--force", action="store_true", help="replace an existing plan")

    rn = sub.add_parser("run", help="dispatcher: work through .relay/plan.md step by step in a worktree")
    rs = rn.add_subparsers(dest="action", required=True)
    st = rs.add_parser("start", help="start a run in the foreground")
    st.add_argument("--plan", help="plan file (default: <project>/.relay/plan.md)")
    st.add_argument("--yes", action="store_true", help="the plan is approved; do not wait for approval")
    rs.add_parser("list", help="runs, newest first")
    for name, text in (("show", "one run as JSON"), ("stop", "stop a run after killing its agent"),
                       ("approve", "approve the plan or [confirm] step a run waits at"),
                       ("remove", "delete a finished run's worktree and state (its branch stays)")):
        x = rs.add_parser(name, help=text)
        x.add_argument("id")

    a = p.parse_args(argv)
    if a.cmd == "plan":
        from relay import dispatch
        cfg = config.load()
        return dispatch.draft(cfg, a.task, cfg["project_root"], a.force)
    if a.cmd == "run":
        return _run(a)
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
        install.install(a.agent, a.autocompact, not a.no_statusline, not a.no_rule)
        return 0
    if a.cmd == "uninstall":
        install.uninstall(a.agent, a.autocompact)
        return 0
    return 2


def _run(a):
    import os
    import sys

    from relay import dispatch, plan
    if a.action == "start":
        cfg = config.load()
        path = a.plan or os.path.join(cfg["project_root"], ".relay", "plan.md")
        if not os.path.isfile(path):
            print(f"no plan at {path}; write one or run: relay plan \"<task>\"", file=sys.stderr)
            return 2
        try:
            run = dispatch.Run(config.load(os.path.dirname(os.path.abspath(path))), path, approved=a.yes)
        except plan.PlanError as e:
            print(f"plan: {e}", file=sys.stderr)
            return 2
        return run.start()
    if a.action == "list":
        return dispatch.print_runs(dispatch.list_runs())
    if a.action == "show":
        r = dispatch.load(a.id)
        print(json.dumps(r, indent=2, ensure_ascii=False) if r else f"no run {a.id}")
        return 0 if r else 1
    if a.action == "stop":
        ok = dispatch.request_stop(a.id)
        print("stop requested" if ok else f"no run {a.id}")
        return 0 if ok else 1
    if a.action == "approve":
        gate = dispatch.approve(a.id)
        print(f"approved: {gate}" if gate else f"run {a.id} is not waiting for approval")
        return 0 if gate else 1
    if a.action == "remove":
        ok = dispatch.remove(a.id)
        print("removed" if ok else f"run {a.id} is unknown or still active")
        return 0 if ok else 1
    return 2
