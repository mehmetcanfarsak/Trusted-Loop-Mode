---
description: Manage the judge ensemble (list / add / remove) in judges.json
argument-hint: 'list | add --id <id> --format openai|anthropic --endpoint <url> --model <m> --key-env <ENV> [--temperature 0.0] [--threshold 0.5] | remove --id <id>'
allowed-tools: Bash
---

Manage the independent judge ensemble. Decorrelation is the whole point: pick
judges from **different families/providers** (e.g. one OpenAI + one Anthropic);
two decorrelated judges beat three similar ones. Never make the generator's own
family the decisive judge. Keep temperature ≈ 0. API keys are referenced by
environment-variable **name** only — never paste a key value.

Map the user's intent to one of:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/core/manage.py" judges-list
python3 "${CLAUDE_PLUGIN_ROOT}/core/manage.py" judges-add --id <id> --format <openai|anthropic> --endpoint <url> --model <model> --key-env <ENV_VAR_NAME> [--temperature 0.0] [--threshold 0.5]
python3 "${CLAUDE_PLUGIN_ROOT}/core/manage.py" judges-remove --id <id>
```

Raw arguments: `$ARGUMENTS`

Relay the command output to the user.
