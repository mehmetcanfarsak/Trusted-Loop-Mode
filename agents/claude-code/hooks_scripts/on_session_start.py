#!/usr/bin/env python3
"""
Claude Code SessionStart adapter (thin).

On compact/resume, re-injects the loop briefing via ``additionalContext`` so the
agent regains its bearings. Best-effort only — correctness depends on state.json,
never on this (§12). Never raises.
"""
import json
import os
import sys

_CORE = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "core"))
sys.path.insert(0, _CORE)

import common  # noqa: E402


def main():
    try:
        raw = sys.stdin.read()
        hook_input = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        return
    try:
        cwd = hook_input.get("cwd") or os.getcwd()
        briefing = common.session_briefing(cwd, os.environ)
    except Exception:
        return
    if briefing:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": briefing,
        }}))


if __name__ == "__main__":  # pragma: no cover
    main()
