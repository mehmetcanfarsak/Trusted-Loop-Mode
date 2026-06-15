#!/usr/bin/env python3
"""
Trusted-Loop Mode — CLI backing the slash commands.

Subcommands (see commands/*.md):
  set-goal "<goal>" [--criteria "..."]... [--checks "cmd"]...
  clear-goal
  status
  judges-list
  judges-add --id ID --format openai|anthropic --endpoint URL --model M
             --key-env ENV [--temperature T] [--threshold X]
  judges-remove --id ID

cwd is resolved from TRUSTED_LOOP_CWD, CLAUDE_CWD, or the process cwd
(agent-neutral first). Returns 0 on success, 1 on usage/runtime error.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common
import judges as judges_mod

_FORMATS = ("openai", "anthropic")


def _resolve_cwd(env):
    return env.get("TRUSTED_LOOP_CWD") or env.get("CLAUDE_CWD") or os.getcwd()


def _collect(args, flag):
    """Pull every ``--flag value`` pair out of args; returns the values list."""
    values = []
    i = 0
    rest = []
    while i < len(args):
        if args[i] == flag:
            if i + 1 >= len(args):
                raise ValueError("{} requires a value".format(flag))
            values.append(args[i + 1])
            i += 2
        else:
            rest.append(args[i])
            i += 1
    args[:] = rest
    return values


def _opt(args, flag, default=None):
    if flag in args:
        i = args.index(flag)
        if i + 1 >= len(args):
            raise ValueError("{} requires a value".format(flag))
        value = args[i + 1]
        del args[i:i + 2]
        return value
    return default


def cmd_set_goal(args, cwd, out):
    criteria = _collect(args, "--criteria")
    checks = _collect(args, "--checks")
    goal = " ".join(a for a in args if a).strip()
    if not goal:
        print("Usage: set-goal \"<goal>\" [--criteria \"...\"]... [--checks \"cmd\"]...",
              file=sys.stderr)
        return 1
    state = common.default_state()
    state.update({
        "active": True,
        "mode": "goal",
        "goal": goal,
        "criteria": criteria or ["fully satisfy the stated goal"],
        "checks": checks,
        "started_at": time.time(),
    })
    common.save_state(cwd, state)
    print("Trusted-Loop armed (mode=goal).", file=out)
    print("Goal: {}".format(goal), file=out)
    print("Criteria: {}".format(state["criteria"]), file=out)
    print("Checks: {}".format(checks or "(none)"), file=out)
    return 0


def cmd_clear_goal(args, cwd, out):
    state = common.load_state(cwd)
    was_active = state.get("active")
    state["active"] = False
    common.save_state(cwd, state)
    print("Trusted-Loop disarmed." if was_active else "Trusted-Loop was not armed.",
          file=out)
    return 0


def cmd_status(args, cwd, out):
    common.expire_if_over_budget(cwd, os.environ)
    state = common.load_state(cwd)
    judges = judges_mod.load_judges(cwd)
    print("active:    {}".format(state.get("active")), file=out)
    print("mode:      {}".format(state.get("mode")), file=out)
    print("goal:      {}".format(state.get("goal")), file=out)
    print("iteration: {}".format(state.get("iteration", 0)), file=out)
    print("criteria:  {}".format(state.get("criteria", [])), file=out)
    print("checks:    {}".format(state.get("checks", [])), file=out)
    print("judges:    {}".format([j.get("id") for j in judges]), file=out)
    if state.get("last_reason"):
        print("last_reason:\n{}".format(state["last_reason"]), file=out)
    return 0


def cmd_judges_list(args, cwd, out):
    judges = judges_mod.load_judges(cwd)
    if not judges:
        print("No judges configured. Add one with: judges-add ...", file=out)
        return 0
    for j in judges:
        print("- {id} [{format}] {model} @ {endpoint} key_env={key} "
              "temp={temp} threshold={th} enabled={en}".format(
                  id=j.get("id"), format=j.get("format"), model=j.get("model"),
                  endpoint=j.get("endpoint"), key=j.get("api_key_env"),
                  temp=j.get("temperature", judges_mod.DEFAULT_TEMPERATURE),
                  th=j.get("confidence_threshold", judges_mod.DEFAULT_THRESHOLD),
                  en=j.get("enabled", True)), file=out)
    return 0


def cmd_judges_add(args, cwd, out):
    try:
        jid = _opt(args, "--id")
        fmt = _opt(args, "--format")
        endpoint = _opt(args, "--endpoint")
        model = _opt(args, "--model")
        key_env = _opt(args, "--key-env")
        temperature = _opt(args, "--temperature", str(judges_mod.DEFAULT_TEMPERATURE))
        threshold = _opt(args, "--threshold", str(judges_mod.DEFAULT_THRESHOLD))
    except ValueError as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        return 1
    missing = [n for n, v in (("--id", jid), ("--format", fmt), ("--endpoint", endpoint),
                              ("--model", model), ("--key-env", key_env)) if not v]
    if missing:
        print("Error: missing required {}".format(", ".join(missing)), file=sys.stderr)
        return 1
    if fmt not in _FORMATS:
        print("Error: --format must be one of {}".format(_FORMATS), file=sys.stderr)
        return 1
    try:
        temperature = float(temperature)
        threshold = float(threshold)
    except ValueError:
        print("Error: --temperature and --threshold must be numbers", file=sys.stderr)
        return 1
    judges = [j for j in judges_mod.load_judges(cwd) if j.get("id") != jid]
    judges.append({
        "id": jid, "format": fmt, "endpoint": endpoint, "model": model,
        "api_key_env": key_env, "temperature": temperature,
        "confidence_threshold": threshold, "enabled": True,
    })
    judges_mod.save_judges(cwd, judges)
    print("Added judge {!r} ({}). Ensemble size: {}.".format(jid, fmt, len(judges)),
          file=out)
    return 0


def cmd_judges_remove(args, cwd, out):
    try:
        jid = _opt(args, "--id")
    except ValueError as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        return 1
    if not jid:
        print("Error: judges-remove requires --id", file=sys.stderr)
        return 1
    judges = judges_mod.load_judges(cwd)
    kept = [j for j in judges if j.get("id") != jid]
    judges_mod.save_judges(cwd, kept)
    if len(kept) != len(judges):
        print("Removed judge {!r}. Ensemble size: {}.".format(jid, len(kept)), file=out)
    else:
        print("No judge with id {!r}.".format(jid), file=out)
    return 0


_COMMANDS = {
    "set-goal": cmd_set_goal,
    "clear-goal": cmd_clear_goal,
    "status": cmd_status,
    "judges-list": cmd_judges_list,
    "judges-add": cmd_judges_add,
    "judges-remove": cmd_judges_remove,
}


def main(argv=None, env=None, out=None):
    argv = sys.argv if argv is None else argv
    env = os.environ if env is None else env
    out = sys.stdout if out is None else out
    args = list(argv[1:])
    if not args or args[0] not in _COMMANDS:
        print("Usage: manage.py {{{}}} ...".format("|".join(_COMMANDS)), file=sys.stderr)
        return 1
    sub = args.pop(0)
    cwd = _resolve_cwd(env)
    return _COMMANDS[sub](args, cwd, out)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
