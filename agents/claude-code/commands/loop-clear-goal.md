---
description: Disarm Trusted-Loop (allow the agent to stop normally)
allowed-tools: Bash
---

Disarm the verification loop. After this, Stop behaves like normal Claude Code.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/core/manage.py" clear-goal
```

Report the result to the user.
