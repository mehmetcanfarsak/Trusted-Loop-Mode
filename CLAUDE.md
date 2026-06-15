# Operating contract — this project runs under Trusted-Loop Mode

A verification loop may be armed for this project. When it is, **you will not be
allowed to stop** when you *think* you are done: an independent ensemble of judge
models re-checks the work against a **fixed definition of done**, grounded in
**fresh behavioral evidence** (test/build/lint output, tool results, diffs) — not
your own claims. If a judge cites a real, unmet criterion, the Stop hook hands you
a precise continuation instruction and you keep working.

Work with the loop, not against it:

- **Recover the live goal after a compaction.** Your context may be summarized.
  The durable anchor is `state.json`, not your memory. Run
  `python3 ${CLAUDE_PLUGIN_ROOT}/core/manage.py status` (or `/loop-status`) and
  read `.claude/trusted-loop/checkpoint.json` to re-establish the goal, the fixed
  criteria, and the standing feedback.
- **Run the checks yourself before declaring done.** Don't assert completion from
  memory — actually run the project's checks and read the output.
- **Re-run after any later edit.** Evidence goes stale: a check that passed before
  an edit no longer proves anything. Stale evidence is rejected.
- **Report in terms of evidence, not confidence.** "tests pass: `pytest` exited 0,
  142 passed" — not "I'm confident it works."
- **Honor the criteria as the fixed scope.** Do not expand scope, invent new
  requirements, or gold-plate. The criteria are the contract.
- **Never weaken, skip, or delete tests to make a check pass.** That defeats the
  entire mechanism and will be caught.
- **If you are genuinely blocked, or an action would be destructive, say so and
  stop** — do not fabricate progress. Honesty halts the loop cleanly; fabrication
  wastes iterations and risks damage.
- **Never echo secrets.** The transcript is sent to external judge providers
  (scrubbed best-effort, but not guaranteed).

The hard caps (max iterations, wall-clock) and stuck-detection are the real
safety net; the judge ensemble is the quality knob. The loop will always
eventually stop — make those iterations count.
