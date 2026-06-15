#!/usr/bin/env python3
"""
Claude Code Stop / SubagentStop adapter (thin).

Reads the hook JSON on stdin, delegates to the CLI-agnostic gate in core/, and
translates the neutral decision into Claude Code's Stop contract:
  - block  → print {"decision":"block","reason":...} to stdout, exit 0
  - allow  → emit nothing, exit 0

Fails OPEN: any error allows the stop, so the agent is never trapped.
Invoke with --subagent for the SubagentStop hook.
"""
import json
import os
import sys

_CORE = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "core"))
sys.path.insert(0, _CORE)

import gate  # noqa: E402


def main(argv=None):
    argv = sys.argv if argv is None else argv
    subagent = "--subagent" in argv[1:]
    try:
        raw = sys.stdin.read()
        hook_input = json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        return
    try:
        decision = gate.run_stop_gate(hook_input, env=os.environ, subagent=subagent)
    except Exception:
        return  # fail open
    if decision.get("action") == "block":
        print(json.dumps({"decision": "block", "reason": decision.get("reason", "")}))


if __name__ == "__main__":  # pragma: no cover
    main()
