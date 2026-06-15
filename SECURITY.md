# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.x     | ✅        |

## Reporting a vulnerability

Please **do not open a public issue** for security vulnerabilities.

Report privately using GitHub's
[private vulnerability reporting](https://github.com/mehmetcanfarsak/Trusted-Loop-Mode/security/advisories/new)
(Security → Report a vulnerability). Include:

- A description of the vulnerability and its impact
- Steps to reproduce
- Affected file(s) or component(s)
- Any suggested fix, if you have one

Expect an initial response within **7 days**. Confirmed issues are prioritized and
you'll be credited in the release notes unless you prefer to remain anonymous.

## Threat model

Trusted-Loop is a local Claude Code plugin whose hook scripts:

- read JSON from stdin (hook input);
- read and write files under `<project>/.claude/trusted-loop/`;
- **run your configured checks as shell commands** (`subprocess`, `shell=True`)
  in the project directory;
- **send a scrubbed, truncated transcript plus fresh check output over the network**
  to each configured judge endpoint (`urllib`), and optionally POST to
  `NOTIFY_WEBHOOK`.

Key considerations:

- **Your code leaves the machine.** The transcript and check output are sent to N
  external judge providers. **Secret scrubbing is best-effort** (regex over common
  key shapes — `sk-…`, `sk-ant-…`, `AKIA…`, `ghp_…`, `xox…`, `bearer …`,
  `key=/token=/password=`, JWTs) and **is not a guarantee**. For sensitive repos,
  prefer no-retention endpoints and/or a local judge, and review the scrubber
  against your secret formats.
- **API keys are referenced by environment-variable name only** and are never
  written to `judges.json` or any other file. They are read from the environment
  at call time. Do not paste key values into configs.
- **Checks execute arbitrary shell.** Whatever you put in `--checks` runs
  unattended on every Stop. Only arm checks you trust; treat `state.json` as
  trusted input (it is written by the plugin, not by the agent).
- **The gate fails open.** On any error — bad JSON, judge timeout, all judges
  failing — the hook allows the stop and never traps the agent. This means a
  broken gate silently stops *enforcing* verification; confirm the loop is
  working if it matters to you.
- **Unattended runs auto-commit.** A best-effort `git` checkpoint commits the
  working tree each iteration. Review those commits; run in a branch.
- **Sandboxing is the operator's job.** Run unattended loops with appropriate
  Claude Code `permissions.deny` / `sandbox` settings. The plugin is a quality
  gate, not a security boundary, and must not be relied on to contain an
  adversarial agent.
- **In-tree state.** `.claude/trusted-loop/` holds `state.json`, `judges.json`
  (env-var names only), `decisions.jsonl`, and reports. It is gitignored by
  default; `decisions.jsonl` may contain transcript-derived snippets.

## Out of scope

- Vulnerabilities in Claude Code itself (report to Anthropic)
- Vulnerabilities in judge providers, `jq`, Python, or other system dependencies
- Data retention / training policies of third-party judge endpoints you configure
