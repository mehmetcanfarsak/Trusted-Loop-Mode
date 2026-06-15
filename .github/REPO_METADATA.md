# Repository metadata (for maximum discoverability)

GitHub's repository **description** and **topics** are the single biggest levers
for GitHub search ranking and for surfacing in Google / LLM search results. They
live in GitHub's settings, not in the repo files, so apply them once after
pushing.

## Recommended description

Paste into **Settings → General → Description** (the "About" sidebar):

```
Keep an unattended Claude Code agent working until an independent ensemble of LLM judges confirms the task is done — verified against fresh test/build output, not the agent's own claim.
```

## Recommended website

```
https://github.com/mehmetcanfarsak/Trusted-Loop-Mode#readme
```

## Recommended topics

Add via the "About" gear → Topics (GitHub allows up to 20), ordered by search
value:

```
claude-code
claude-code-plugin
claude-code-hooks
stop-hook
ai-coding-agent
ai-agents
autonomous-agents
unattended-agents
agent-verification
completion-verification
llm-as-judge
llm-judge
judge-ensemble
llm-evaluation
anthropic
openai
developer-tools
ai-productivity
prompt-engineering
python
```

(20 topics — the GitHub maximum. They are kept consistent with `keywords` in
`plugin.json` / `marketplace.json` and `codemeta.json` so every discovery surface
reinforces the same terms.)

## Apply with the GitHub CLI

Once `gh` is installed and authenticated (`gh auth login`):

```bash
gh repo edit mehmetcanfarsak/Trusted-Loop-Mode \
  --description "Keep an unattended Claude Code agent working until an independent ensemble of LLM judges confirms the task is done — verified against fresh test/build output, not the agent's own claim." \
  --homepage "https://github.com/mehmetcanfarsak/Trusted-Loop-Mode#readme" \
  --add-topic claude-code,claude-code-plugin,claude-code-hooks,stop-hook,ai-coding-agent,ai-agents,autonomous-agents,unattended-agents,agent-verification,completion-verification,llm-as-judge,llm-judge,judge-ensemble,llm-evaluation,anthropic,openai,developer-tools,ai-productivity,prompt-engineering,python
```

## Other discoverability checklist

- [ ] Set description + topics (above)
- [ ] Enable **Discussions**
- [ ] Add a social preview image (Settings → General → Social preview) — drives click-through from search and social shares
- [ ] After first release, create a GitHub **Release** tagged `v0.1.0` (Releases are indexed and rank well)
- [ ] Confirm `llms.txt` is served at the repo root (already present) for LLM crawlers
