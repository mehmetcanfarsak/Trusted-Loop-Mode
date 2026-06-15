# Codex adapter (not yet implemented)

Placeholder for a Codex CLI adapter. Trusted-Loop's engine (`core/`) is
CLI-agnostic; only the per-CLI hook I/O translation lives under `agents/<cli>/`.

See `agents/opencode/README.md` for the adapter contract — a Codex adapter would
follow the same shape, delegating to `core.gate.run_stop_gate`,
`core.common.write_checkpoint`, and `core.common.session_briefing`, and
translating the neutral decision into Codex's stop-control mechanism.
