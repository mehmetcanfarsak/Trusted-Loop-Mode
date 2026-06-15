# Trusted-Loop Mode — keep an unattended AI coding agent working until the job is *verifiably* done

> **A Claude Code plugin that stops an AI coding agent from quitting early.** It
> gates the agent's Stop with an independent **ensemble of LLM judges** that
> verify completion against **fresh test/build/lint evidence** — not the agent's
> own claim of "done".

[![tests](https://img.shields.io/badge/tests-154%20passing-brightgreen)](tests/run_tests.py)
[![coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)](Makefile)
[![Python](https://img.shields.io/badge/python-3-blue)](#development)
[![deps](https://img.shields.io/badge/runtime%20deps-stdlib%20only-blue)](#development)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Trusted-Loop Mode keeps a coding agent working on a long-running, **unattended**
task **until an independent ensemble of judge models confirms the work is done** —
judged against a *fixed* definition of done and **fresh behavioral evidence**
(test/build/lint output, tool results, diffs), not the agent's own claims of
completion. It is built for tasks you can't babysit at the CLI: "make the suite
green", large build-outs, and research loops.

When the agent tries to stop, the Stop hook re-runs your deterministic checks
*now*, asks every configured judge (independently, in parallel) whether the fixed
criteria are met, and either lets the agent stop or feeds it a precise,
evidence-backed instruction to continue.

> **The judge is a reader, not an agent.** It never calls tools. It reasons over
> the transcript (every tool call and result the agent already produced) plus
> fresh ground truth that the *hook* gathered by re-running your checks.

## Architecture

CLI-agnostic engine in `core/`; thin per-CLI adapters under `agents/<cli>/`. v0.1
ships the **Claude Code** adapter; `agents/opencode/` and `agents/codex/` are
placeholders documenting the contract for future CLIs.

```
core/      common.py · judges.py · gate.py · manage.py   (stdlib only, 100% covered)
agents/claude-code/   commands/ · hooks/hooks.json · hooks_scripts/ · setup.sh
```

All heavy logic lives in `core/`; each adapter only translates its CLI's
stdin/stdout to the neutral decision `core.gate.run_stop_gate` returns.

## Install

Via marketplace (recommended):

```
/plugin marketplace add mehmetcanfarsak/Trusted-Loop-Mode
/plugin install trusted-loop@trusted-loop
```

Or directly into a project / globally with the installer:

```
make install-project PROJECT=/path/to/repo
make install-global
```

## Usage

```
/loop-judges add --id openai-gpt --format openai \
  --endpoint https://api.openai.com/v1 --model gpt-4o --key-env OPENAI_API_KEY
/loop-judges add --id anthropic-claude --format anthropic \
  --endpoint https://api.anthropic.com --model claude-opus-4-8 --key-env ANTHROPIC_API_KEY

/loop-set-goal "make the suite green" --criteria "all unit tests pass" --checks "pytest -q"
```

Now let the agent work. When it tries to stop, the gate verifies against fresh
`pytest -q` output and the judges; it stops only when they agree. `/loop-status`
shows live state; `/loop-clear-goal` disarms.

API keys are referenced by **environment-variable name** only — they are read from
your environment at call time and never written to disk.

## Ensemble guidance (§10)

The only reason to run more than one judge is **decorrelation**:

- Pick judges from **different families / providers / scales**. Two decorrelated
  judges beat three similar ones; gains saturate fast (a 4th rarely helps).
- **Never** make the generator's own family (Claude) the *decisive* judge —
  self-preference bias.
- Pin temperature ≈ 0. In an approve-to-stop gate a high-variance judge can only
  wrongly *block*, so it is strictly harmful.

## Unattended-run safety (§11)

- **Hard caps** (iterations, wall-clock) finalize regardless of the judges — the
  cap is the real safety, the ensemble is the quality knob. The wall-clock cap is
  enforced on *every* state read, so an idle over-budget loop disarms itself.
- **Stuck detection** via an evidence hash (check results + transcript tool-call
  count); also catches compaction thrashing.
- **Git checkpoint** each iteration (best-effort; skipped silently if not a repo).
- **Standing feedback is replaced, never appended.**
- **Secret scrubbing** before the transcript leaves the machine — best-effort
  regex (sk-…, sk-ant-…, AKIA…, ghp_…, xox…, bearer …, `key=/token=/password=`,
  JWTs). **Not a guarantee.** For sensitive repos, prefer no-retention endpoints
  and/or a local judge.
- **Judge failure is safe** → allow stop, never trap the agent.
- **Human escalation:** set `NOTIFY_WEBHOOK` to be POSTed on stuck / finalize /
  judge-error.
- **Sandboxing is the operator's job.** Run unattended loops with appropriate
  `permissions.deny` / `sandbox` settings; review the auto-commits the loop makes.

## Configuration (`userConfig`, prompted on enable)

`MAX_ITERATIONS` (12) · `WALL_CLOCK_MINUTES` (120) · `STUCK_LIMIT` (3) ·
`VERIFY_WITHOUT_GOAL` (false) · `NOTIFY_WEBHOOK` (optional, sensitive). The judge
chain is **not** in `userConfig` (it can't express a dynamic per-judge list) —
manage it with `/loop-judges`, which writes `.claude/trusted-loop/judges.json`.

All runtime state lives **in-tree** under `.claude/trusted-loop/` (gitignored), so
an unattended run is transparent: watch `decisions.jsonl` grow, read the live
`state.json`, inspect `last_report.json`.

## Evaluation plan (§15)

Treat the system as a **safety-vs-usefulness tradeoff**, not pass/fail. The loop
generates its own eval set in `decisions.jsonl`: build a small labelled set of
transcripts (complete vs confidently-wrong), score each candidate judge on
accuracy/precision/recall **and pairwise error-correlation**, keep the smallest
decorrelated set that clears your target, and tune thresholds + iteration cap to
move along the completed / failures-caught / wrongly-rejected curve.

## Prior art — what's de-risked vs unverified

This plugin's hook mechanics were cross-checked against two shipping plugins:

- **De-risked:** the Stop contract — print `{"decision":"block","reason":...}` to
  stdout, exit 0 to keep the agent working — is used by OpenAI's
  `codex-plugin-cc`, and the in-tree `.claude/...` state + atomic-write +
  per-prompt re-injection patterns come from `brainstorm-mode`.
- **Unverified / best-effort:** `SessionStart` `additionalContext` injection and
  `PreCompact` behaviour are used by neither reference and are not documented as
  stable APIs. Correctness here depends on `state.json`, **never** on injection —
  if your build sees these hooks evolve, validate before relying on them.

## FAQ

**How do I stop a Claude Code agent from quitting before the work is finished?**
Install Trusted-Loop Mode, add at least one judge with `/loop-judges`, and arm a
goal with `/loop-set-goal`. The `Stop` hook then re-runs your checks and asks the
judge ensemble whether the fixed criteria are met against that fresh output; if
not, the agent is handed a precise, evidence-cited instruction and keeps working.

**Why does an AI agent claim "all tests pass" when they don't?**
Its transcript can be stale (a check passed before a later edit) or its
self-assessment over-optimistic. Trusted-Loop re-runs the checks at stop time and
trusts the fresh exit codes over the agent's claim, so a real failure blocks
completion.

**What is an LLM-judge ensemble, and why run more than one judge?**
Each judge is one stateless model call that verifies completion. The only reason
to run several is **decorrelation** — judges from different families make
different mistakes, so a defect one misses another may catch. Two decorrelated
judges beat three similar ones; gains saturate fast. Never make the agent's own
model family the decisive judge (self-preference bias); keep temperature ≈ 0.

**How do I keep an AI agent running unattended until a task is done?**
Arm a goal with deterministic checks. Hard caps (max iterations, wall-clock) and
stuck-detection guarantee the loop always eventually stops, while the judge
ensemble decides whether "done" is real. Set `NOTIFY_WEBHOOK` to be pinged on
finalize/stuck/judge-error so "unattended" can mean "notify me when you need me".

**Does it send my code to external providers? Is that safe?**
The scrubbed, truncated transcript and fresh check output go to each configured
judge endpoint. Secret scrubbing is best-effort (not a guarantee); for sensitive
repos prefer no-retention endpoints or a local judge. API keys are referenced by
env-var name and never written to disk. Run unattended loops in a sandbox.

**How is this different from a code-review bot or a correctness prover?**
It is neither — it is a *completion gate* checking whether a fixed, user-stated
definition of done is met against fresh evidence. Not an adversarial monitor, not
a correctness prover, not a substitute for code review.

**Can I use it with OpenCode or OpenAI Codex?**
Yes — the `core/` engine is agent-agnostic. Add a thin adapter under
`agents/<name>/` that calls `core.gate.run_stop_gate` and translates the neutral
decision into that CLI's "keep going" contract. OpenCode and Codex stubs are
included.

## Known limitations (§17)

- A reader judge verifies only what's in the transcript + fresh checks; it cannot
  independently audit **test quality**.
- Hook field names, `userConfig` schema, and `SessionStart`/`PreCompact` behaviour
  evolve and aren't documented as stable — validate before relying.
- Free judge endpoints are rate-limited and may retain/train on inputs.
- Reference implementation — **test in a sandbox before unattended use.**

## Development

```
make test       # stdlib unittest, no network
make coverage    # enforces 100% line coverage (dev-only coverage.py)
```

Runtime is **standard library only** (hooks run with no `pip install`).
`coverage.py` is a dev-only dependency. MIT licensed.
