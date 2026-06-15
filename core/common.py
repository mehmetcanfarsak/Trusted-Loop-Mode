"""
Trusted-Loop Mode — shared, CLI-agnostic helpers.

Standard library only (no pip): paths, atomic writes, durable state, runtime
options, transcript parsing, secret scrubbing, deterministic checks, the
decision log, webhook notification, finalize, and pre-compaction checkpoints.

All persistent state lives IN-TREE under ``<cwd>/.claude/trusted-loop/`` so an
unattended run is transparent and inspectable; the whole directory is gitignored
on first write. If ``cwd`` is unwritable, fall back to
``~/.trusted-loop/<cwd-hash>/``.
"""
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request

STATE_VERSION = 1
STATE_DIR_NAME = os.path.join(".claude", "trusted-loop")
CHECK_TIMEOUT_SEC = 90
TRANSCRIPT_TAIL_CHARS = 60000
CHECK_TAIL_CHARS = 4000

# ── Runtime options (userConfig → CLAUDE_PLUGIN_OPTION_<NAME> env) ─────────────
# (default, caster)
_OPTION_SPECS = {
    "MAX_ITERATIONS": (12, int),
    "WALL_CLOCK_MINUTES": (120, int),
    "STUCK_LIMIT": (3, int),
    "VERIFY_WITHOUT_GOAL": (False, "bool"),
    "NOTIFY_WEBHOOK": ("", str),
}


def _cast(value, caster):
    if caster == "bool":
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    try:
        return caster(value)
    except (TypeError, ValueError):
        raise ValueError(value)


def get_option(name, env=None):
    """Resolve one runtime option from CLAUDE_PLUGIN_OPTION_<name>, else default."""
    env = os.environ if env is None else env
    default, caster = _OPTION_SPECS[name]
    raw = env.get("CLAUDE_PLUGIN_OPTION_" + name)
    if raw is None or raw == "":
        return default
    try:
        return _cast(raw, caster)
    except ValueError:
        return default


# ── Paths ─────────────────────────────────────────────────────────────────────

def _cwd_hash(cwd):
    try:
        canonical = os.path.realpath(cwd)
    except OSError:
        canonical = cwd
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def state_dir(cwd):
    """In-tree state directory, creating it (with a gitignore) on first use.

    Falls back to ``~/.trusted-loop/<cwd-hash>/`` if the in-tree location cannot
    be created (e.g. read-only checkout).
    """
    primary = os.path.join(cwd, STATE_DIR_NAME)
    try:
        os.makedirs(primary, exist_ok=True)
        _ensure_gitignore(primary)
        return primary
    except OSError:
        fallback = os.path.join(os.path.expanduser("~"), ".trusted-loop", _cwd_hash(cwd))
        os.makedirs(fallback, exist_ok=True)
        return fallback


def _ensure_gitignore(directory):
    """Drop a ``*`` gitignore so in-tree state never enters version control."""
    path = os.path.join(directory, ".gitignore")
    if not os.path.exists(path):
        try:
            with open(path, "w") as f:
                f.write("*\n")
        except OSError:
            pass


def state_path(cwd):
    return os.path.join(state_dir(cwd), "state.json")


def judges_path(cwd):
    return os.path.join(state_dir(cwd), "judges.json")


def decisions_path(cwd):
    return os.path.join(state_dir(cwd), "decisions.jsonl")


def checkpoint_path(cwd):
    return os.path.join(state_dir(cwd), "checkpoint.json")


def report_path(cwd):
    return os.path.join(state_dir(cwd), "last_report.json")


# ── Atomic write ──────────────────────────────────────────────────────────────

def atomic_write(path, data):
    """Write ``data`` (a JSON-serialisable object) atomically: temp file in the
    target directory, then ``os.replace``. Cleans up the temp file on failure."""
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── Durable state ─────────────────────────────────────────────────────────────

def default_state():
    return {
        "version": STATE_VERSION,
        "active": False,
        "mode": None,
        "goal": None,
        "criteria": [],
        "anchor_request": None,
        "iteration": 0,
        "started_at": None,
        "last_evidence_hash": None,
        "stuck_count": 0,
        "last_reason": None,
        "checks": [],
    }


def load_state(cwd):
    """Return durable state, merged over defaults and never raising.

    A pure defensive read (no side effects): corrupt or missing state yields
    defaults. Wall-clock enforcement is explicit via ``expire_if_over_budget``.
    """
    path = state_path(cwd)
    state = default_state()
    try:
        with open(path) as f:
            parsed = json.load(f)
        if isinstance(parsed, dict):
            state.update(parsed)
            state["version"] = STATE_VERSION
    except (OSError, ValueError):
        pass
    return state


def _wall_clock_exceeded(state, env):
    started = state.get("started_at")
    if not started:
        return False
    minutes = get_option("WALL_CLOCK_MINUTES", env)
    return (time.time() - started) > minutes * 60


def expire_if_over_budget(cwd, env=None):
    """Finalize and disarm an armed loop that has exceeded its wall-clock budget.

    Called on reads outside the gate (session briefing, status) so an idle
    over-budget loop auto-disarms even if the Stop gate never runs again.
    Returns True if it disarmed a loop.
    """
    state = load_state(cwd)
    if state.get("active") and _wall_clock_exceeded(state, env):
        finalize(cwd, "timeout", "wall-clock budget exceeded", env=env, state=state)
        return True
    return False


def save_state(cwd, state):
    state["version"] = STATE_VERSION
    atomic_write(state_path(cwd), state)
    return state


# ── Transcript ────────────────────────────────────────────────────────────────

def read_transcript(path):
    """Read a JSONL transcript into a list of event dicts. Never raises."""
    events = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return events


def _content_blocks(event):
    message = event.get("message", event)
    if not isinstance(message, dict):
        return []
    content = message.get("content", [])
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return content
    return []


def flatten_transcript(events):
    """Flatten events to ``(text, tool_call_count)``.

    ``text`` concatenates message text and a compact note of each tool call /
    result; ``tool_call_count`` counts ``tool_use`` blocks (used by the evidence
    hash for stuck detection).
    """
    parts = []
    tool_calls = 0
    for event in events:
        for block in _content_blocks(event):
            if not isinstance(block, dict):
                if isinstance(block, str):
                    parts.append(block)
                continue
            btype = block.get("type")
            if btype == "text":
                parts.append(str(block.get("text", "")))
            elif btype == "tool_use":
                tool_calls += 1
                parts.append("[tool_use {} {}]".format(
                    block.get("name", ""), json.dumps(block.get("input", {}), default=str)))
            elif btype == "tool_result":
                parts.append("[tool_result {}]".format(
                    _stringify(block.get("content", ""))))
    text = "\n".join(p for p in parts if p)
    if len(text) > TRANSCRIPT_TAIL_CHARS:
        text = text[-TRANSCRIPT_TAIL_CHARS:]
    return text, tool_calls


def last_user_request(events):
    """Return the text of the most recent genuine user message, or None.

    Skips tool-result turns (role ``user`` carrying ``tool_result`` blocks) so
    infer-mode anchors on what the human actually asked for.
    """
    for event in reversed(events):
        role = event.get("role") or (event.get("message", {}) or {}).get("role")
        if role != "user":
            continue
        blocks = _content_blocks(event)
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks):
            continue
        texts = [str(b.get("text", "")) for b in blocks
                 if isinstance(b, dict) and b.get("type") == "text"]
        texts += [b for b in blocks if isinstance(b, str)]
        joined = "\n".join(t for t in texts if t).strip()
        if joined:
            return joined
    return None


def _stringify(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for item in content:
            if isinstance(item, dict):
                out.append(str(item.get("text", item)))
            else:
                out.append(str(item))
        return " ".join(out)
    return str(content)


# ── Secret scrubbing ──────────────────────────────────────────────────────────

_REDACTED = "[REDACTED]"

# (compiled pattern, replacement). Token-shaped secrets are fully redacted;
# labelled ``key=value`` / ``Bearer <token>`` forms keep the label via a
# captured prefix group so the scrub stays readable.
_SCRUB_RULES = [
    (re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), _REDACTED),  # JWT
    (re.compile(r"sk-ant-[A-Za-z0-9_-]+"), _REDACTED),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), _REDACTED),
    (re.compile(r"AKIA[0-9A-Z]{16}"), _REDACTED),
    (re.compile(r"ghp_[A-Za-z0-9]{36}"), _REDACTED),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]+"), _REDACTED),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+"), r"\1" + _REDACTED),
    (re.compile(r"(?i)((?:api[_-]?key|token|password|secret)\s*[=:]\s*)\S+"), r"\1" + _REDACTED),
]


def scrub(text):
    """Best-effort removal of common secret shapes. Not a guarantee."""
    for pattern, replacement in _SCRUB_RULES:
        text = pattern.sub(replacement, text)
    return text


# ── Deterministic checks (fresh evidence) ─────────────────────────────────────

def run_checks(checks, cwd):
    """Run each shell command, capturing ``(command, exit_code, tail)`` now."""
    results = []
    for command in checks:
        try:
            proc = subprocess.run(
                command, shell=True, cwd=cwd, capture_output=True,
                text=True, timeout=CHECK_TIMEOUT_SEC,
            )
            output = (proc.stdout or "") + (proc.stderr or "")
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            output = "check timed out after {}s".format(CHECK_TIMEOUT_SEC)
            exit_code = 124
        except Exception as exc:  # pragma: no cover - defensive
            output = "check failed to run: {}".format(exc)
            exit_code = 127
        results.append({
            "command": command,
            "exit_code": exit_code,
            "tail": output[-CHECK_TAIL_CHARS:],
        })
    return results


def evidence_hash(checks, tool_call_count):
    """Stable fingerprint of ``[(command, exit_code)]`` + transcript tool count."""
    payload = {
        "checks": [[c["command"], c["exit_code"]] for c in checks],
        "tool_calls": tool_call_count,
    }
    blob = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ── Decision log / notify / finalize ──────────────────────────────────────────

def log_decision(cwd, record):
    """Append one JSON object to ``decisions.jsonl`` (best-effort)."""
    record = dict(record)
    record.setdefault("ts", time.time())
    try:
        with open(decisions_path(cwd), "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except OSError:
        pass


def notify(event, env=None):
    """POST ``event`` to NOTIFY_WEBHOOK if configured (best-effort, never raises)."""
    url = get_option("NOTIFY_WEBHOOK", env)
    if not url:
        return False
    try:
        data = json.dumps(event, default=str).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=10).close()
        return True
    except Exception:
        return False


def finalize(cwd, status, detail="", env=None, state=None):
    """Write ``last_report.json``, log, notify, then disarm and persist state."""
    if state is None:
        state = load_state(cwd)
    started = state.get("started_at")
    elapsed = (time.time() - started) if started else 0.0
    report = {
        "status": status,
        "detail": detail,
        "iterations": state.get("iteration", 0),
        "elapsed_sec": round(elapsed, 3),
        "ts": time.time(),
    }
    try:
        atomic_write(report_path(cwd), report)
    except OSError:  # pragma: no cover - defensive
        pass
    log_decision(cwd, {"phase": "finalize", "decision": status, "detail": detail})
    notify({"event": "finalize", "cwd": cwd, **report}, env)
    state["active"] = False
    save_state(cwd, state)
    return report


# ── Pre-compaction checkpoint ─────────────────────────────────────────────────

def write_checkpoint(cwd, state, remaining_work=""):
    checkpoint = {
        "goal": state.get("goal"),
        "criteria": state.get("criteria", []),
        "iteration": state.get("iteration", 0),
        "remaining_work": remaining_work or state.get("last_reason") or "",
        "saved_at": time.time(),
    }
    try:
        atomic_write(checkpoint_path(cwd), checkpoint)
    except OSError:  # pragma: no cover - defensive
        return None
    return checkpoint


def read_checkpoint(cwd):
    try:
        with open(checkpoint_path(cwd)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ── Session briefing (SessionStart re-injection) ──────────────────────────────

def _report_seen_path(cwd):
    return os.path.join(state_dir(cwd), "report_seen")


def _pop_report_notice(cwd):
    """Return ``last_report.json`` once, the first session after it is written.

    Uses a ``report_seen`` marker holding the report timestamp so the same
    finalize is announced at most once (a one-shot tombstone).
    """
    report = read_report(cwd)
    if not report:
        return None
    ts = str(report.get("ts", ""))
    try:
        with open(_report_seen_path(cwd)) as f:
            if f.read().strip() == ts:
                return None
    except OSError:
        pass
    try:
        with open(_report_seen_path(cwd), "w") as f:
            f.write(ts)
    except OSError:  # pragma: no cover - defensive
        pass
    return report


def read_report(cwd):
    try:
        with open(report_path(cwd)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def session_briefing(cwd, env=None):
    """Assemble the SessionStart ``additionalContext`` string, or None.

    Active loop → a recovery briefing (goal, fixed criteria, checks, standing
    feedback). Otherwise → a one-shot note that a previous loop finished.
    """
    expire_if_over_budget(cwd, env)
    state = load_state(cwd)
    if state.get("active"):
        lines = [
            "[Trusted-Loop] A verification loop is ACTIVE for this project. You "
            "will not be allowed to stop until an independent ensemble of judges "
            "agrees the criteria are met, judged against fresh evidence.",
            "Goal: {}".format(state.get("goal")),
        ]
        criteria = state.get("criteria") or []
        if criteria:
            lines.append("Criteria (fixed scope — do not expand):")
            lines += ["  - {}".format(c) for c in criteria]
        checks = state.get("checks") or []
        if checks:
            lines.append("Run these checks yourself before declaring done: {}"
                         .format(", ".join(checks)))
        lines.append("Iteration so far: {}".format(state.get("iteration", 0)))
        if state.get("last_reason"):
            lines.append("Standing feedback from the last verification:\n{}"
                         .format(state["last_reason"]))
        lines.append("Recover full state any time: python3 "
                     "${CLAUDE_PLUGIN_ROOT}/core/manage.py status")
        return "\n".join(lines)

    report = _pop_report_notice(cwd)
    if report:
        return ("[Trusted-Loop] The previous verification loop ended: status={!r} "
                "({}) after {} iteration(s).".format(
                    report.get("status"), report.get("detail", ""),
                    report.get("iterations", 0)))
    return None


# ── Git checkpoint (best-effort) ──────────────────────────────────────────────

def git_checkpoint(cwd, label="iteration"):
    """Commit the working tree so unattended progress is never lost.

    Best-effort: silently no-ops when ``cwd`` is not a git repo, there is
    nothing to commit, or git is unavailable. Never blocks the loop.
    """
    try:
        inside = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=10)
        if inside.returncode != 0:
            return False
        subprocess.run(["git", "-C", cwd, "add", "-A"],
                       capture_output=True, text=True, timeout=15)
        committed = subprocess.run(
            ["git", "-C", cwd, "commit", "-m", "trusted-loop: " + label],
            capture_output=True, text=True, timeout=15)
        return committed.returncode == 0
    except Exception:
        return False
