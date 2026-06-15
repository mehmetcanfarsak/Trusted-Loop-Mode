#!/usr/bin/env python3
"""
Claude Code PreCompact adapter (thin).

Writes a briefing to checkpoint.json before the lossy summarization so the agent
can recover its bearings after compaction. Cannot make the agent act; correctness
depends on state.json, not on this (§12). Best-effort, never raises.
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
        state = common.load_state(cwd)
        if state.get("active"):
            common.write_checkpoint(cwd, state)
    except Exception:
        pass


if __name__ == "__main__":  # pragma: no cover
    main()
