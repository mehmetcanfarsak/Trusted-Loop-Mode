# Contributing to Trusted-Loop Mode

Thanks for your interest in contributing. This document covers bug reports,
feature requests, and the two most common contribution paths: adding a new
**coding-CLI adapter** and adding a new **judge provider**.

---

## Table of contents

- [Reporting bugs](#reporting-bugs)
- [Requesting features](#requesting-features)
- [Development setup](#development-setup)
- [Architecture in one minute](#architecture-in-one-minute)
- [Adding a new CLI adapter](#adding-a-new-cli-adapter)
- [Adding a new judge provider](#adding-a-new-judge-provider)
- [Pull request process](#pull-request-process)
- [Code standards](#code-standards)

---

## Reporting bugs

Use the [Bug Report](https://github.com/mehmetcanfarsak/Trusted-Loop-Mode/issues/new?template=bug_report.yml)
issue template. Include:

- Which CLI adapter you are using (claude-code, or a stub)
- The exact command, hook, or judge config involved
- What you expected vs. what happened
- Your Python version (`python3 --version`)
- The relevant `decisions.jsonl` line(s) and `last_report.json`, **with any
  sensitive content removed** (paths, session IDs, repo names)

## Requesting features

Use the [Feature Request](https://github.com/mehmetcanfarsak/Trusted-Loop-Mode/issues/new?template=feature_request.yml)
template. Before opening one, check it against the project's **non-goals**: this
is a *completion gate*, not an adversarial/red-team monitor, not a correctness
prover, and not a substitute for code review. Proposals that turn the judge into
a tool-using agent are out of scope — the judge is a reader by design.

## Development setup

```bash
git clone https://github.com/mehmetcanfarsak/Trusted-Loop-Mode
cd Trusted-Loop-Mode

make test        # run the suite (no install, no network)
make coverage    # enforce 100% line coverage
```

Requirements: **Python 3.8+** (stdlib only); `jq` only for `setup.sh`. The suite
mocks `urllib` and `subprocess`, so it runs fully offline with no API keys.

### Manually exercising a hook

Hook scripts read JSON from stdin. Drive one directly:

```bash
echo '{"cwd":"/tmp/scratch","transcript_path":null}' \
  | python3 agents/claude-code/hooks_scripts/on_stop.py
```

With no loop armed it prints nothing and exits 0 (allow stop). Arm a goal and add
a judge via `core/manage.py` to see the gate engage.

## Architecture in one minute

```
core/                       Agent-agnostic engine (stdlib only, 100% covered)
  common.py                 paths, in-tree state, atomic writes, scrub, checks, finalize
  judges.py                 judge HTTP clients, parallel eval, evidence-based aggregation
  gate.py                   the Stop orchestration → neutral decision
  manage.py                 CLI backing the slash commands
agents/<cli>/               Thin per-CLI adapters (I/O translation only)
```

The rule: **all logic lives in `core/`; adapters only translate a CLI's
stdin/stdout to the neutral decision `core.gate.run_stop_gate` returns.**

## Adding a new CLI adapter

1. Create `agents/<cli>/hooks_scripts/` with thin entry scripts that:
   - parse that CLI's stop event and call `core.gate.run_stop_gate(...)`;
   - translate the neutral `{"action":"block","reason":...}` / `{"action":"allow"}`
     into that CLI's "keep working" contract;
   - **fail open** — any error must allow the stop, never trap the agent.
2. Mirror the checkpoint and session-briefing adapters
   (`core.common.write_checkpoint`, `core.common.session_briefing`) if the CLI
   has compaction / session-start equivalents.
3. Add a `setup.sh` and wire the adapter into `tests/run_tests.py` (load its
   modules under distinct names so coverage measures them).
4. `core/` must not change. See `agents/opencode/README.md` for the full contract.

## Adding a new judge provider

Most providers are OpenAI- or Anthropic-compatible and need no code. For a genuly
new wire format, extend `core/judges.py`:

- add a branch to `build_request(...)` (URL, headers, body) and `parse_response(...)`;
- keep the response parse tolerant (reuse `extract_json`);
- never read or store a key value — judges reference an **env-var name** only;
- add mocked-HTTP tests covering success, HTTP error, timeout, and malformed JSON.

## Pull request process

Use the PR template. Before requesting review:

- `make test` is green and `make coverage` reports **100%**;
- no third-party runtime dependency was added (`core/` stays stdlib-only);
- every hook path fails open;
- docs / README / CHANGELOG updated where relevant.

## Code standards

- Standard library only at runtime; `coverage.py` is the sole dev dependency.
- Match the surrounding style (naming, comment density, `.format` over f-strings
  where the file already does so).
- New code ships with tests that hold the suite at 100% line coverage.
- `# pragma: no cover` is reserved for `if __name__ == "__main__":` guards.
