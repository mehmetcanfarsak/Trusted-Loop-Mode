"""
Trusted-Loop Mode — the Stop / SubagentStop orchestrator (§9).

CLI-agnostic. ``run_stop_gate`` reads the hook input (already parsed to a dict),
runs the full gate algorithm, performs all side effects (fresh checks, judge
ensemble, logging, checkpoints, finalize), and returns a NEUTRAL decision:

    {"action": "block", "reason": "<continuation instruction>"}
    {"action": "allow"}

Per-CLI adapters translate this neutral decision into their own stdout contract.
Every failure path returns ``allow`` — the loop must never trap the agent.
"""
import os
import time

import common
import judges as judges_mod

ALLOW = {"action": "allow"}


def _block(reason):
    return {"action": "block", "reason": reason}


def run_stop_gate(hook_input, env=None, subagent=False):
    """Run the gate for one Stop attempt. Returns a neutral decision dict."""
    env = os.environ if env is None else env
    cwd = hook_input.get("cwd") or os.getcwd()
    transcript_path = hook_input.get("transcript_path")
    phase = "subagent_stop" if subagent else "stop"

    state = common.load_state(cwd)

    # 1. arming
    if not state.get("active"):
        if not common.get_option("VERIFY_WITHOUT_GOAL", env):
            return ALLOW
        events = common.read_transcript(transcript_path) if transcript_path else []
        req = common.last_user_request(events)
        if not req:
            return ALLOW
        state = common.default_state()
        state.update({
            "active": True,
            "mode": "infer",
            "goal": req,
            "criteria": ["fully satisfy the user's request"],
            "anchor_request": req,
            "started_at": time.time(),
        })
        common.save_state(cwd, state)

    # judges configured?
    all_judges = judges_mod.load_judges(cwd)
    if not judges_mod.enabled_judges(all_judges):
        common.finalize(cwd, "no_judges", "no enabled judges configured", env=env, state=state)
        return ALLOW

    # 2. hard caps
    if state.get("iteration", 0) >= common.get_option("MAX_ITERATIONS", env):
        common.finalize(cwd, "max_iterations", "iteration cap reached", env=env, state=state)
        return ALLOW
    if common._wall_clock_exceeded(state, env):
        common.finalize(cwd, "timeout", "wall-clock budget exceeded", env=env, state=state)
        return ALLOW

    # 3. fresh evidence + stuck detection
    events = common.read_transcript(transcript_path) if transcript_path else []
    text, tool_calls = common.flatten_transcript(events)
    transcript_text = common.scrub(text)
    checks = common.run_checks(state.get("checks", []), cwd)
    h = common.evidence_hash(checks, tool_calls)
    if h == state.get("last_evidence_hash"):
        state["stuck_count"] = state.get("stuck_count", 0) + 1
    else:
        state["stuck_count"] = 0
    state["last_evidence_hash"] = h
    if state["stuck_count"] >= common.get_option("STUCK_LIMIT", env):
        common.save_state(cwd, state)
        common.finalize(cwd, "stuck", "no observable change across iterations",
                        env=env, state=state)
        return ALLOW

    # 4. judges (independent, parallel)
    results, agg = judges_mod.evaluate(
        all_judges, lambda name: env.get(name, ""),
        state.get("goal"), state.get("criteria", []), checks, transcript_text)
    common.log_decision(cwd, {
        "phase": phase,
        "iteration": state.get("iteration", 0),
        "decision": agg["decision"],
        "evidence_hash": h,
        "judges": [{"id": r.get("id"), "verdict": r.get("verdict"),
                    "confidence": r.get("confidence"), "error": r.get("error")}
                   for r in results],
    })

    # 5. act
    if agg["decision"] == "complete":
        common.git_checkpoint(cwd, "complete")
        common.finalize(cwd, "complete", "judges agree the criteria are met",
                        env=env, state=state)
        return ALLOW
    if agg["decision"] == "error":
        common.finalize(cwd, "judge_error", "no judge returned a usable verdict",
                        env=env, state=state)
        return ALLOW

    # block → keep working
    state["iteration"] = state.get("iteration", 0) + 1
    state["last_reason"] = agg["reason"]
    common.save_state(cwd, state)
    common.git_checkpoint(cwd, "blocked-iter-{}".format(state["iteration"]))
    return _block(agg["reason"])
