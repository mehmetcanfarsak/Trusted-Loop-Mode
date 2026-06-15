---
description: Arm Trusted-Loop with a goal, fixed criteria, and deterministic checks
argument-hint: '"<goal>" [--criteria "..."]... [--checks "npm test"]...'
allowed-tools: Bash
---

Arm the trusted verification loop. The agent will not be allowed to stop until an
independent ensemble of judges agrees the criteria are met, judged against fresh
check output — not the agent's own claims.

Run exactly this, forwarding the user's arguments verbatim:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/core/manage.py" set-goal $ARGUMENTS
```

Then briefly confirm to the user what was armed (goal, criteria, checks). Remind
them that at least one judge must be configured (`/loop-judges list`) or the loop
will disarm itself and allow stopping.
