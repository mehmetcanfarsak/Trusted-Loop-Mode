"""
Trusted-Loop Mode — the judge ensemble.

Each judge is one stateless chat-completion call to an external model that reads
(1) a FIXED definition of done, (2) fresh deterministic-check results, and (3) a
scrubbed transcript, then returns a strict-JSON verdict. Judges run
independently and in parallel — no judge sees another's verdict (decorrelation).
Aggregation is by EVIDENCE, not votes (§8.4): a confident, evidence-cited
"incomplete" blocks; an evidence-free objection does not.

Standard library only: urllib for HTTP, concurrent.futures for parallelism.
"""
import json
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import common  # core/ is placed on sys.path by adapters, manage.py, and tests

DEFAULT_TEMPERATURE = 0.0
DEFAULT_THRESHOLD = 0.5
DEFAULT_MAX_TOKENS = 1024
HTTP_TIMEOUT_SEC = 60

SYSTEM_PROMPT = """\
You are a strict, evidence-based completion verifier for a coding agent.

You are given: (1) a FIXED definition of done, (2) fresh results from
deterministic checks (tests/build/lint) run just now, and (3) a transcript of
the agent's actions and the tool results it received.

Your ONLY job is to decide whether the fixed criteria are met. Rules:
- Judge against the stated criteria ONLY. Do NOT invent new requirements, raise
  scope, or suggest improvements beyond what was asked.
- Trust BEHAVIORAL EVIDENCE (test/build output, tool results, diffs) over the
  agent's own claims. If the agent says "all tests pass" but the fresh check
  shows a failure, it is INCOMPLETE.
- Watch for STALE evidence: if a check passed earlier but the agent edited files
  afterward without re-running it, treat that criterion as unverified.
- If you mark anything incomplete, you MUST cite the specific evidence (which
  check failed, which tool result, which missing action). No evidence, no
  objection.

Respond with STRICT JSON only, no prose, no markdown fences:
{
  "verdict": "complete" | "incomplete",
  "confidence": 0.0-1.0,
  "criteria": [{"name":"...","met":true,"evidence":"..."}],
  "blocking_evidence": "concise evidence-backed statement of what remains, or empty string if complete"
}"""


# ── Config round-trip (judges.json) ───────────────────────────────────────────

def load_judges(cwd):
    """Return the list of judge configs (empty list if unset / unreadable)."""
    try:
        with open(common.judges_path(cwd)) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    judges = data.get("judges") if isinstance(data, dict) else None
    return judges if isinstance(judges, list) else []


def save_judges(cwd, judges):
    common.atomic_write(common.judges_path(cwd), {"judges": judges})
    return judges


def enabled_judges(judges):
    return [j for j in judges if j.get("enabled", True)]


# ── Robust JSON extraction ────────────────────────────────────────────────────

def extract_json(text):
    """Extract the outermost ``{...}`` object from a model response.

    Tolerates code fences and preamble. Returns a dict, or None if no balanced
    object parses.
    """
    if not text:
        return None
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None


# ── HTTP request builders ─────────────────────────────────────────────────────

def build_request(judge, key, system, user):
    """Build a urllib Request for the judge's provider format."""
    endpoint = judge["endpoint"].rstrip("/")
    model = judge["model"]
    temperature = float(judge.get("temperature", DEFAULT_TEMPERATURE))
    fmt = judge.get("format")
    if fmt == "openai":
        url = endpoint + "/chat/completions"
        body = {
            "model": model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key,
        }
    elif fmt == "anthropic":
        url = endpoint + "/v1/messages"
        body = {
            "model": model,
            "max_tokens": DEFAULT_MAX_TOKENS,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        headers = {
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        }
    else:
        raise ValueError("unknown judge format: {}".format(fmt))
    data = json.dumps(body).encode("utf-8")
    return urllib.request.Request(url, data=data, headers=headers, method="POST")


def parse_response(judge, raw):
    """Pull the text content out of a provider response payload."""
    if judge.get("format") == "openai":
        return raw["choices"][0]["message"]["content"]
    return raw["content"][0]["text"]


# ── One judge call ────────────────────────────────────────────────────────────

def build_user_message(goal, criteria, checks, transcript):
    criteria_block = "\n".join("- {}".format(c) for c in criteria) or "- (none specified)"
    return (
        "FIXED GOAL:\n{goal}\n\n"
        "FIXED DEFINITION OF DONE (criteria):\n{criteria}\n\n"
        "FRESH DETERMINISTIC CHECK RESULTS (run just now):\n{checks}\n\n"
        "AGENT TRANSCRIPT (scrubbed, truncated):\n{transcript}"
    ).format(
        goal=goal or "(inferred from the user's request)",
        criteria=criteria_block,
        checks=json.dumps(checks, indent=2),
        transcript=transcript,
    )


def eval_one(judge, key, goal, criteria, checks, transcript):
    """Evaluate one judge. Returns a result dict; never raises."""
    result = {
        "id": judge.get("id"),
        "verdict": None,
        "confidence": 0.0,
        "blocking_evidence": "",
        "criteria": [],
        "error": None,
    }
    if not key:
        result["error"] = "missing API key (env var {!r} unset)".format(judge.get("api_key_env"))
        return result
    user = build_user_message(goal, criteria, checks, transcript)
    try:
        req = build_request(judge, key, SYSTEM_PROMPT, user)
    except ValueError as exc:
        result["error"] = str(exc)
        return result
    try:
        resp = urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC)
        raw = json.loads(resp.read().decode("utf-8"))
        resp.close()
        content = parse_response(judge, raw)
    except (urllib.error.URLError, OSError) as exc:
        result["error"] = "request failed: {}".format(exc)
        return result
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        result["error"] = "bad response: {}".format(exc)
        return result

    verdict = extract_json(content)
    if not isinstance(verdict, dict) or "verdict" not in verdict:
        result["error"] = "unparseable verdict"
        return result
    result["verdict"] = verdict.get("verdict")
    try:
        result["confidence"] = float(verdict.get("confidence", 0.0))
    except (TypeError, ValueError):
        result["confidence"] = 0.0
    result["blocking_evidence"] = str(verdict.get("blocking_evidence", "") or "")
    result["criteria"] = verdict.get("criteria", []) if isinstance(verdict.get("criteria"), list) else []
    return result


# ── Parallel ensemble evaluation ──────────────────────────────────────────────

def evaluate(judges, key_lookup, goal, criteria, checks, transcript):
    """Run all enabled judges independently and in parallel.

    ``key_lookup`` maps an env-var name to its value (typically
    ``os.environ.get``). Returns ``(results, aggregate)``.
    """
    active = enabled_judges(judges)

    def _run(judge):
        key = key_lookup(judge.get("api_key_env", ""))
        return eval_one(judge, key, goal, criteria, checks, transcript)

    if active:
        with ThreadPoolExecutor(max_workers=len(active)) as pool:
            results = list(pool.map(_run, active))
    else:
        results = []
    return results, aggregate(active, results)


# ── Aggregation by evidence (§8.4) ────────────────────────────────────────────

def _threshold_for(judges, judge_id):
    for j in judges:
        if j.get("id") == judge_id:
            return float(j.get("confidence_threshold", DEFAULT_THRESHOLD))
    return DEFAULT_THRESHOLD


def aggregate(judges, results):
    """Decide complete / block / error from per-judge results, by evidence."""
    ok = [r for r in results if r.get("error") is None and r.get("verdict") is not None]
    if not ok:
        return {"decision": "error", "reason": None, "blockers": []}

    blockers = []
    for r in ok:
        threshold = _threshold_for(judges, r.get("id"))
        if (r.get("verdict") == "incomplete"
                and r.get("blocking_evidence")
                and r.get("confidence", 0.0) >= threshold):
            blockers.append(r["blocking_evidence"].strip())

    if blockers:
        return {"decision": "block", "reason": build_reason(blockers), "blockers": blockers}
    return {"decision": "complete", "reason": None, "blockers": []}


def build_reason(blockers):
    """One fresh continuation instruction from deduped blocking-evidence items."""
    seen = []
    for b in blockers:
        if b and b not in seen:
            seen.append(b)
    lines = ["The task is not yet complete. Address each item, grounded in the evidence:"]
    lines += ["{}. {}".format(i + 1, item) for i, item in enumerate(seen)]
    lines.append("Re-run the checks after your edits to confirm. "
                 "Do not add anything beyond the original criteria.")
    return "\n".join(lines)
