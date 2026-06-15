# OpenCode adapter (not yet implemented)

Trusted-Loop's engine lives in `core/` and is **CLI-agnostic**. This directory is
a placeholder for an OpenCode adapter that would mirror
`agents/claude-code/`: thin hook entry scripts that parse OpenCode's stdin/stdout
contract and delegate to `core/`.

To add it, implement the same three responsibilities as the Claude Code adapter:

| Responsibility | Claude Code adapter | What OpenCode needs |
|---|---|---|
| Gate a stop attempt | `hooks_scripts/on_stop.py` → `core.gate.run_stop_gate(...)` | Parse OpenCode's stop event; translate the neutral `{"action":"block","reason":...}` / `{"action":"allow"}` into OpenCode's "keep going" contract. |
| Checkpoint before compaction | `on_precompact.py` → `core.common.write_checkpoint(...)` | OpenCode's pre-compaction equivalent, if any. |
| Re-inject the briefing | `on_session_start.py` → `core.common.session_briefing(...)` | OpenCode's session-start context-injection mechanism. |

`core/` does not change. Only the I/O translation is per-CLI.
