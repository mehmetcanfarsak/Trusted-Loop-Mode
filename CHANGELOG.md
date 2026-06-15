# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-06-15

Initial release.

### Added

- **Evidence-grounded Stop gate for Claude Code.** A `Stop` / `SubagentStop` hook
  intercepts the agent's attempt to stop, re-runs the configured deterministic
  checks *now*, and asks an independent ensemble of judge models whether a fixed
  definition of done is met — blocking the stop with one evidence-cited
  continuation instruction, or allowing it.
- **Judge ensemble** (`core/judges.py`) — OpenAI-format and Anthropic-format
  chat-completion clients over `urllib`, run independently and in parallel
  (`ThreadPoolExecutor`) so no judge sees another's verdict (decorrelation).
  Aggregation is **by evidence, not votes**: a confident, evidence-cited
  "incomplete" blocks; an evidence-free objection does not.
- **CLI-agnostic architecture** — all logic in `core/` (`common`, `judges`,
  `gate`, `manage`); thin per-CLI adapters under `agents/<cli>/`. Claude Code is
  implemented; OpenCode and Codex adapter contracts are stubbed.
- **Slash commands** — `/loop-set-goal`, `/loop-clear-goal`, `/loop-status`, and
  `/loop-judges` (list/add/remove), backed by `core/manage.py`.
- **Unattended-run safety** — hard caps (max iterations, wall-clock), wall-clock
  enforced on every state read, stuck-detection via an evidence hash, best-effort
  per-iteration git checkpoint, best-effort secret scrubbing before the transcript
  leaves the machine, fail-open on judge error, and an optional `NOTIFY_WEBHOOK`
  for human escalation on stuck / finalize / judge-error.
- **In-tree state** under `<project>/.claude/trusted-loop/` (gitignored), with
  atomic writes, a defensive `version`-stamped `state.json`, a self-generated eval
  log (`decisions.jsonl`), pre-compaction `checkpoint.json`, and `last_report.json`.
  API keys are referenced by env-var **name** only and never written to disk.
- **Compaction handling** — `PreCompact` writes a briefing; `SessionStart`
  (compact/resume) re-injects it via `additionalContext`; correctness depends on
  `state.json`, never on injection.
- **`CLAUDE.md`** durable operating contract for the agent under the loop.
- **Distribution** — `.claude-plugin/marketplace.json` + `plugin.json` (with
  `userConfig`), and an idempotent `setup.sh` (`--project` / `--global` /
  `--uninstall`).
- **Tests** — 154 tests, **100% line coverage**, stdlib `unittest` only (network
  and subprocess fully mocked); enforced by `make coverage`.
- **Docs & metadata** — README (with FAQ), CONTRIBUTING, SECURITY, LICENSE (MIT),
  GitHub issue/PR templates, and a CI workflow testing Python 3.8–3.13.
- **Discovery / search optimization** — `llms.txt` (LLM answer-engine summary with
  FAQ), `codemeta.json` (schema.org/JSON-LD software metadata), `CITATION.cff`,
  a `$schema`-validated `plugin.json` with `displayName` + discovery `keywords`,
  marketplace `keywords` + `category` + `tags`, and a `REPO_METADATA.md` with the
  recommended GitHub description and topics — all sharing one consistent keyword set.

[Unreleased]: https://github.com/mehmetcanfarsak/Trusted-Loop-Mode/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/mehmetcanfarsak/Trusted-Loop-Mode/releases/tag/v0.1.0
