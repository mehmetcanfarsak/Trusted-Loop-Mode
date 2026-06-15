#!/usr/bin/env python3
"""
Trusted-Loop Mode test suite. Standard library only; no network, no real judges.

    python3 tests/run_tests.py

Tests call module functions / ``main()`` directly (patching stdin/stdout and
mocking ``urllib``/``subprocess``) so coverage instruments every line. Run under
coverage with ``make coverage`` (enforces 100%).
"""
import io
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).parent.parent
CORE = REPO_ROOT / "core"
HOOKS = REPO_ROOT / "agents" / "claude-code" / "hooks_scripts"
for p in (str(CORE), str(HOOKS)):
    if p not in sys.path:
        sys.path.insert(0, p)

import common
import judges as judges_mod
import gate
import manage
import on_stop
import on_precompact
import on_session_start


# ── Helpers ───────────────────────────────────────────────────────────────────

class FakeResp:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._data

    def close(self):
        pass


def openai_payload(verdict_json):
    return {"choices": [{"message": {"content": verdict_json}}]}


def anthropic_payload(verdict_json):
    return {"content": [{"text": verdict_json}]}


def verdict_json(verdict="complete", confidence=0.9, evidence=""):
    return json.dumps({
        "verdict": verdict, "confidence": confidence,
        "criteria": [{"name": "c", "met": verdict == "complete", "evidence": "e"}],
        "blocking_evidence": evidence,
    })


def make_judge(jid="oai", fmt="openai", key_env="OPENAI_API_KEY", threshold=0.5):
    return {
        "id": jid, "format": fmt, "endpoint": "https://example.test/v1",
        "model": "m", "api_key_env": key_env, "temperature": 0.0,
        "confidence_threshold": threshold, "enabled": True,
    }


def write_transcript(cwd, events):
    path = os.path.join(cwd, "transcript.jsonl")
    with open(path, "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    return path


def call_main(module, stdin_str, argv=None):
    buf = io.StringIO()
    with mock.patch.object(sys, "stdin", io.StringIO(stdin_str)):
        with redirect_stdout(buf):
            if argv is not None:
                module.main(argv)
            else:
                module.main()
    return buf.getvalue()


# ── common: options ───────────────────────────────────────────────────────────

class TestOptions(unittest.TestCase):
    def test_default(self):
        self.assertEqual(common.get_option("MAX_ITERATIONS", {}), 12)

    def test_set_int(self):
        self.assertEqual(common.get_option("MAX_ITERATIONS",
                         {"CLAUDE_PLUGIN_OPTION_MAX_ITERATIONS": "5"}), 5)

    def test_empty_falls_back(self):
        self.assertEqual(common.get_option("MAX_ITERATIONS",
                         {"CLAUDE_PLUGIN_OPTION_MAX_ITERATIONS": ""}), 12)

    def test_invalid_int_falls_back(self):
        self.assertEqual(common.get_option("MAX_ITERATIONS",
                         {"CLAUDE_PLUGIN_OPTION_MAX_ITERATIONS": "abc"}), 12)

    def test_bool_true(self):
        self.assertTrue(common.get_option("VERIFY_WITHOUT_GOAL",
                        {"CLAUDE_PLUGIN_OPTION_VERIFY_WITHOUT_GOAL": "TRUE"}))

    def test_bool_false(self):
        self.assertFalse(common.get_option("VERIFY_WITHOUT_GOAL",
                         {"CLAUDE_PLUGIN_OPTION_VERIFY_WITHOUT_GOAL": "no"}))

    def test_default_env_is_os_environ(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            self.assertIsInstance(common.get_option("MAX_ITERATIONS"), int)

    def test_string_option(self):
        self.assertEqual(common.get_option("NOTIFY_WEBHOOK",
                         {"CLAUDE_PLUGIN_OPTION_NOTIFY_WEBHOOK": "http://x"}), "http://x")


# ── common: paths / gitignore / atomic write ─────────────────────────────────

class TestPaths(unittest.TestCase):
    def test_state_dir_in_tree_with_gitignore(self):
        with tempfile.TemporaryDirectory() as cwd:
            d = common.state_dir(cwd)
            self.assertTrue(d.endswith(os.path.join(".claude", "trusted-loop")))
            gi = os.path.join(d, ".gitignore")
            self.assertEqual(Path(gi).read_text(), "*\n")

    def test_gitignore_not_rewritten(self):
        with tempfile.TemporaryDirectory() as cwd:
            d = common.state_dir(cwd)
            Path(d, ".gitignore").write_text("custom")
            common.state_dir(cwd)  # second call must not overwrite
            self.assertEqual(Path(d, ".gitignore").read_text(), "custom")

    def test_gitignore_open_oserror_suppressed(self):
        with tempfile.TemporaryDirectory() as cwd:
            with mock.patch("builtins.open", side_effect=OSError("ro")):
                common._ensure_gitignore(cwd)  # must not raise

    def test_state_dir_fallback_when_primary_unwritable(self):
        with tempfile.TemporaryDirectory() as cwd, tempfile.TemporaryDirectory() as home:
            real_makedirs = os.makedirs

            def fake_makedirs(path, *a, **kw):
                if ".claude" in path:
                    raise OSError("read-only")
                return real_makedirs(path, *a, **kw)

            with mock.patch.object(common.os, "makedirs", side_effect=fake_makedirs):
                with mock.patch.object(common.os.path, "expanduser", return_value=home):
                    d = common.state_dir(cwd)
            self.assertTrue(d.startswith(home))

    def test_cwd_hash_realpath_oserror(self):
        with mock.patch.object(common.os.path, "realpath", side_effect=OSError):
            h = common._cwd_hash("/some/path")
        self.assertEqual(len(h), 16)

    def test_atomic_write_roundtrip(self):
        with tempfile.TemporaryDirectory() as cwd:
            p = os.path.join(cwd, "x.json")
            common.atomic_write(p, {"a": 1})
            self.assertEqual(json.loads(Path(p).read_text()), {"a": 1})

    def test_atomic_write_reraises_and_cleans_tmp(self):
        with tempfile.TemporaryDirectory() as cwd:
            p = os.path.join(cwd, "x.json")
            with mock.patch.object(common.json, "dump", side_effect=IOError("full")):
                with self.assertRaises(IOError):
                    common.atomic_write(p, {"a": 1})
            self.assertEqual(list(Path(cwd).glob("*.tmp")), [])

    def test_atomic_write_unlink_oserror_suppressed(self):
        with tempfile.TemporaryDirectory() as cwd:
            p = os.path.join(cwd, "x.json")
            with mock.patch.object(common.json, "dump", side_effect=IOError("full")):
                with mock.patch.object(common.os, "unlink", side_effect=OSError):
                    with self.assertRaises(IOError):
                        common.atomic_write(p, {"a": 1})


# ── common: state load/save + caps ────────────────────────────────────────────

class TestState(unittest.TestCase):
    def test_missing_returns_default(self):
        with tempfile.TemporaryDirectory() as cwd:
            self.assertFalse(common.load_state(cwd)["active"])

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as cwd:
            s = common.default_state()
            s["goal"] = "g"
            s["active"] = True
            common.save_state(cwd, s)
            loaded = common.load_state(cwd)
            self.assertEqual(loaded["goal"], "g")
            self.assertEqual(loaded["version"], common.STATE_VERSION)

    def test_corrupt_returns_default(self):
        with tempfile.TemporaryDirectory() as cwd:
            Path(common.state_path(cwd)).write_text("{broken")
            self.assertFalse(common.load_state(cwd)["active"])

    def test_non_dict_json_returns_default(self):
        with tempfile.TemporaryDirectory() as cwd:
            Path(common.state_path(cwd)).write_text("[]")
            self.assertFalse(common.load_state(cwd)["active"])

    def test_wall_clock_not_exceeded_without_started(self):
        self.assertFalse(common._wall_clock_exceeded({"started_at": None}, {}))

    def test_wall_clock_exceeded(self):
        old = {"started_at": time.time() - 10 ** 9}
        self.assertTrue(common._wall_clock_exceeded(old, {}))

    def test_expire_disarms_over_budget(self):
        with tempfile.TemporaryDirectory() as cwd:
            s = common.default_state()
            s.update({"active": True, "started_at": time.time() - 10 ** 9})
            common.save_state(cwd, s)
            self.assertTrue(common.expire_if_over_budget(cwd, {}))
            self.assertFalse(common.load_state(cwd)["active"])
            self.assertEqual(common.read_report(cwd)["status"], "timeout")

    def test_expire_noop_when_within_budget(self):
        with tempfile.TemporaryDirectory() as cwd:
            s = common.default_state()
            s.update({"active": True, "started_at": time.time()})
            common.save_state(cwd, s)
            self.assertFalse(common.expire_if_over_budget(cwd, {}))

    def test_expire_noop_when_inactive(self):
        with tempfile.TemporaryDirectory() as cwd:
            self.assertFalse(common.expire_if_over_budget(cwd, {}))


# ── common: transcript ────────────────────────────────────────────────────────

class TestTranscript(unittest.TestCase):
    def test_read_skips_blank_and_corrupt(self):
        with tempfile.TemporaryDirectory() as cwd:
            p = os.path.join(cwd, "t.jsonl")
            Path(p).write_text('\n{"a":1}\n{bad\n')
            events = common.read_transcript(p)
            self.assertEqual(events, [{"a": 1}])

    def test_read_missing_file(self):
        self.assertEqual(common.read_transcript("/no/such/file"), [])

    def test_flatten_text_tooluse_toolresult(self):
        events = [
            {"message": {"content": [{"type": "text", "text": "hi"}]}},
            {"message": {"content": [{"type": "tool_use", "name": "Bash",
                                      "input": {"command": "ls"}}]}},
            {"message": {"content": [{"type": "tool_result", "content": "ok"}]}},
        ]
        text, n = common.flatten_transcript(events)
        self.assertIn("hi", text)
        self.assertIn("tool_use Bash", text)
        self.assertIn("tool_result", text)
        self.assertEqual(n, 1)

    def test_flatten_string_content(self):
        text, n = common.flatten_transcript([{"message": {"content": "plain"}}])
        self.assertIn("plain", text)
        self.assertEqual(n, 0)

    def test_flatten_block_is_bare_string(self):
        text, _ = common.flatten_transcript([{"message": {"content": ["raw"]}}])
        self.assertIn("raw", text)

    def test_flatten_block_non_dict_non_str_skipped(self):
        text, _ = common.flatten_transcript([{"message": {"content": [5]}}])
        self.assertEqual(text, "")

    def test_flatten_unknown_block_type_skipped(self):
        text, _ = common.flatten_transcript(
            [{"message": {"content": [{"type": "image"}]}}])
        self.assertEqual(text, "")

    def test_flatten_content_non_list_non_str(self):
        self.assertEqual(common._content_blocks({"message": {"content": 5}}), [])

    def test_flatten_message_not_dict(self):
        self.assertEqual(common._content_blocks({"message": "x"}), [])

    def test_flatten_truncates(self):
        big = "x" * (common.TRANSCRIPT_TAIL_CHARS + 100)
        text, _ = common.flatten_transcript([{"message": {"content": big}}])
        self.assertEqual(len(text), common.TRANSCRIPT_TAIL_CHARS)

    def test_stringify_list_and_dict(self):
        self.assertEqual(common._stringify([{"text": "a"}, "b"]), "a b")
        self.assertIn("type", common._stringify({"type": "x"}))

    def test_last_user_request_text(self):
        events = [
            {"role": "user", "content": [{"type": "text", "text": "please build X"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        ]
        self.assertEqual(common.last_user_request(events), "please build X")

    def test_last_user_request_skips_tool_result(self):
        events = [
            {"message": {"role": "user", "content": [{"type": "text", "text": "real ask"}]}},
            {"message": {"role": "user", "content": [{"type": "tool_result", "content": "x"}]}},
        ]
        self.assertEqual(common.last_user_request(events), "real ask")

    def test_last_user_request_bare_string_block(self):
        events = [{"role": "user", "content": ["hello there"]}]
        self.assertEqual(common.last_user_request(events), "hello there")

    def test_last_user_request_none(self):
        self.assertIsNone(common.last_user_request(
            [{"role": "assistant", "content": [{"type": "text", "text": "x"}]}]))

    def test_last_user_request_empty_text_skipped(self):
        events = [{"role": "user", "content": [{"type": "text", "text": "   "}]}]
        self.assertIsNone(common.last_user_request(events))


# ── common: scrub ─────────────────────────────────────────────────────────────

class TestScrub(unittest.TestCase):
    def test_all_shapes(self):
        jwt = "eyJhbGciOi.eyJzdWIiOiI.abc-_123"
        raw = ("k=sk-ant-abc_DEF token here sk-1234567890ABCDEFGHIJ "
               "AKIAIOSFODNN7EXAMPLE ghp_" + "a" * 36 + " xoxb-1-2-abc "
               "Bearer tok.en-1 password=hunter2 " + jwt)
        out = common.scrub(raw)
        self.assertNotIn("sk-ant-abc", out)
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", out)
        self.assertNotIn("hunter2", out)
        self.assertNotIn("eyJhbGciOi", out)
        self.assertIn("password=[REDACTED]", out)
        self.assertIn("Bearer [REDACTED]", out)


# ── common: checks / evidence hash ────────────────────────────────────────────

class TestChecks(unittest.TestCase):
    def test_run_checks_success(self):
        proc = mock.Mock(stdout="out", stderr="err", returncode=0)
        with mock.patch.object(common.subprocess, "run", return_value=proc):
            res = common.run_checks(["echo hi"], "/tmp")
        self.assertEqual(res[0]["exit_code"], 0)
        self.assertIn("out", res[0]["tail"])

    def test_run_checks_timeout(self):
        with mock.patch.object(common.subprocess, "run",
                               side_effect=common.subprocess.TimeoutExpired("c", 1)):
            res = common.run_checks(["sleep 99"], "/tmp")
        self.assertEqual(res[0]["exit_code"], 124)

    def test_run_checks_empty(self):
        self.assertEqual(common.run_checks([], "/tmp"), [])

    def test_evidence_hash_stable(self):
        a = common.evidence_hash([{"command": "c", "exit_code": 0}], 2)
        b = common.evidence_hash([{"command": "c", "exit_code": 0}], 2)
        c = common.evidence_hash([{"command": "c", "exit_code": 1}], 2)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)


# ── common: log / notify / finalize / checkpoint ──────────────────────────────

class TestLogNotifyFinalize(unittest.TestCase):
    def test_log_decision_appends(self):
        with tempfile.TemporaryDirectory() as cwd:
            common.log_decision(cwd, {"decision": "block"})
            common.log_decision(cwd, {"decision": "complete"})
            lines = Path(common.decisions_path(cwd)).read_text().strip().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertIn("ts", json.loads(lines[0]))

    def test_log_decision_oserror_suppressed(self):
        with tempfile.TemporaryDirectory() as cwd:
            with mock.patch("builtins.open", side_effect=OSError):
                common.log_decision(cwd, {"x": 1})  # must not raise

    def test_notify_no_url(self):
        self.assertFalse(common.notify({"e": 1}, {}))

    def test_notify_success(self):
        env = {"CLAUDE_PLUGIN_OPTION_NOTIFY_WEBHOOK": "http://hook"}
        with mock.patch.object(common.urllib.request, "urlopen") as uo:
            uo.return_value = FakeResp({})
            self.assertTrue(common.notify({"e": 1}, env))

    def test_notify_failure(self):
        env = {"CLAUDE_PLUGIN_OPTION_NOTIFY_WEBHOOK": "http://hook"}
        with mock.patch.object(common.urllib.request, "urlopen",
                               side_effect=urllib.error.URLError("down")):
            self.assertFalse(common.notify({"e": 1}, env))

    def test_finalize_writes_report_and_disarms(self):
        with tempfile.TemporaryDirectory() as cwd:
            s = common.default_state()
            s.update({"active": True, "iteration": 3, "started_at": time.time() - 5})
            common.save_state(cwd, s)
            rep = common.finalize(cwd, "complete", "done", env={}, state=s)
            self.assertEqual(rep["status"], "complete")
            self.assertEqual(rep["iterations"], 3)
            self.assertGreaterEqual(rep["elapsed_sec"], 0)
            self.assertFalse(common.load_state(cwd)["active"])

    def test_finalize_loads_state_when_none(self):
        with tempfile.TemporaryDirectory() as cwd:
            s = common.default_state()
            s["active"] = True
            common.save_state(cwd, s)
            rep = common.finalize(cwd, "stuck", env={})
            self.assertEqual(rep["elapsed_sec"], 0.0)

    def test_checkpoint_roundtrip(self):
        with tempfile.TemporaryDirectory() as cwd:
            s = common.default_state()
            s.update({"goal": "g", "criteria": ["c"], "iteration": 1})
            cp = common.write_checkpoint(cwd, s, "more work")
            self.assertEqual(cp["remaining_work"], "more work")
            self.assertEqual(common.read_checkpoint(cwd)["goal"], "g")

    def test_checkpoint_remaining_from_last_reason(self):
        with tempfile.TemporaryDirectory() as cwd:
            s = common.default_state()
            s["last_reason"] = "fix the test"
            cp = common.write_checkpoint(cwd, s)
            self.assertEqual(cp["remaining_work"], "fix the test")

    def test_read_checkpoint_missing(self):
        with tempfile.TemporaryDirectory() as cwd:
            self.assertIsNone(common.read_checkpoint(cwd))

    def test_read_report_missing(self):
        with tempfile.TemporaryDirectory() as cwd:
            self.assertIsNone(common.read_report(cwd))


# ── common: session briefing + report tombstone ───────────────────────────────

class TestSessionBriefing(unittest.TestCase):
    def test_active_full_briefing(self):
        with tempfile.TemporaryDirectory() as cwd:
            s = common.default_state()
            s.update({"active": True, "goal": "make green", "criteria": ["tests pass"],
                      "checks": ["pytest"], "iteration": 2, "last_reason": "fix x"})
            common.save_state(cwd, s)
            out = common.session_briefing(cwd, {})
            self.assertIn("ACTIVE", out)
            self.assertIn("make green", out)
            self.assertIn("tests pass", out)
            self.assertIn("pytest", out)
            self.assertIn("fix x", out)

    def test_active_minimal_briefing(self):
        with tempfile.TemporaryDirectory() as cwd:
            s = common.default_state()
            s.update({"active": True, "goal": "g"})
            common.save_state(cwd, s)
            out = common.session_briefing(cwd, {})
            self.assertIn("ACTIVE", out)
            self.assertNotIn("Standing feedback", out)

    def test_inactive_with_report_once(self):
        with tempfile.TemporaryDirectory() as cwd:
            common.finalize(cwd, "complete", "all good", env={}, state=common.default_state())
            first = common.session_briefing(cwd, {})
            self.assertIn("previous verification loop ended", first)
            self.assertIsNone(common.session_briefing(cwd, {}))  # one-shot

    def test_inactive_no_report(self):
        with tempfile.TemporaryDirectory() as cwd:
            self.assertIsNone(common.session_briefing(cwd, {}))

    def test_pop_report_notice_none_without_report(self):
        with tempfile.TemporaryDirectory() as cwd:
            self.assertIsNone(common._pop_report_notice(cwd))


# ── common: git checkpoint ────────────────────────────────────────────────────

class TestGitCheckpoint(unittest.TestCase):
    def _proc(self, rc):
        return mock.Mock(returncode=rc, stdout="", stderr="")

    def test_not_a_repo(self):
        with mock.patch.object(common.subprocess, "run", return_value=self._proc(1)):
            self.assertFalse(common.git_checkpoint("/tmp"))

    def test_commit_success(self):
        with mock.patch.object(common.subprocess, "run", return_value=self._proc(0)):
            self.assertTrue(common.git_checkpoint("/tmp", "iter"))

    def test_commit_nothing_to_commit(self):
        seq = [self._proc(0), self._proc(0), self._proc(1)]  # rev-parse, add, commit
        with mock.patch.object(common.subprocess, "run", side_effect=seq):
            self.assertFalse(common.git_checkpoint("/tmp"))

    def test_exception_suppressed(self):
        with mock.patch.object(common.subprocess, "run", side_effect=OSError("no git")):
            self.assertFalse(common.git_checkpoint("/tmp"))


# ── judges: config / json extraction ──────────────────────────────────────────

class TestJudgesConfig(unittest.TestCase):
    def test_load_missing(self):
        with tempfile.TemporaryDirectory() as cwd:
            self.assertEqual(judges_mod.load_judges(cwd), [])

    def test_load_corrupt(self):
        with tempfile.TemporaryDirectory() as cwd:
            Path(common.judges_path(cwd)).write_text("{bad")
            self.assertEqual(judges_mod.load_judges(cwd), [])

    def test_load_non_dict(self):
        with tempfile.TemporaryDirectory() as cwd:
            Path(common.judges_path(cwd)).write_text("[]")
            self.assertEqual(judges_mod.load_judges(cwd), [])

    def test_load_judges_not_list(self):
        with tempfile.TemporaryDirectory() as cwd:
            Path(common.judges_path(cwd)).write_text('{"judges": "x"}')
            self.assertEqual(judges_mod.load_judges(cwd), [])

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as cwd:
            judges_mod.save_judges(cwd, [make_judge()])
            self.assertEqual(judges_mod.load_judges(cwd)[0]["id"], "oai")

    def test_enabled_filter(self):
        js = [make_judge("a"), {**make_judge("b"), "enabled": False}]
        self.assertEqual([j["id"] for j in judges_mod.enabled_judges(js)], ["a"])


class TestExtractJson(unittest.TestCase):
    def test_empty(self):
        self.assertIsNone(judges_mod.extract_json(""))

    def test_fenced(self):
        self.assertEqual(judges_mod.extract_json('```json\n{"a":1}\n```')["a"], 1)

    def test_nested(self):
        self.assertEqual(judges_mod.extract_json('x {"a":{"b":2}} y')["a"]["b"], 2)

    def test_no_brace(self):
        self.assertIsNone(judges_mod.extract_json("no json"))

    def test_brace_inside_string(self):
        self.assertEqual(judges_mod.extract_json('{"a":"}{"}')["a"], "}{")

    def test_escaped_quote_in_string(self):
        self.assertEqual(judges_mod.extract_json(r'{"a":"x\"y"}')["a"], 'x"y')

    def test_backslash_in_string(self):
        self.assertEqual(judges_mod.extract_json(r'{"a":"a\\b"}')["a"], "a\\b")

    def test_first_invalid_second_valid(self):
        self.assertEqual(judges_mod.extract_json('{bad} {"verdict":"complete"}')["verdict"],
                         "complete")

    def test_unbalanced_returns_none(self):
        self.assertIsNone(judges_mod.extract_json('{"a": 1'))


# ── judges: request building / response parsing ───────────────────────────────

class TestJudgeRequests(unittest.TestCase):
    def test_openai_request(self):
        req = judges_mod.build_request(make_judge(), "KEY", "sys", "usr")
        self.assertTrue(req.full_url.endswith("/chat/completions"))
        self.assertEqual(req.headers["Authorization"], "Bearer KEY")

    def test_anthropic_request(self):
        req = judges_mod.build_request(make_judge(fmt="anthropic"), "KEY", "sys", "usr")
        self.assertTrue(req.full_url.endswith("/v1/messages"))
        self.assertEqual(req.headers["X-api-key"], "KEY")

    def test_unknown_format(self):
        with self.assertRaises(ValueError):
            judges_mod.build_request(make_judge(fmt="grok"), "K", "s", "u")

    def test_parse_openai(self):
        self.assertEqual(judges_mod.parse_response(make_judge(), openai_payload("hi")), "hi")

    def test_parse_anthropic(self):
        self.assertEqual(
            judges_mod.parse_response(make_judge(fmt="anthropic"), anthropic_payload("hi")),
            "hi")

    def test_user_message_variants(self):
        msg = judges_mod.build_user_message("g", ["a", "b"], [], "tx")
        self.assertIn("- a", msg)
        empty = judges_mod.build_user_message(None, [], [], "")
        self.assertIn("inferred", empty)
        self.assertIn("(none specified)", empty)


# ── judges: eval_one ──────────────────────────────────────────────────────────

class TestEvalOne(unittest.TestCase):
    def _eval(self, payload=None, raise_exc=None):
        with mock.patch.object(judges_mod.urllib.request, "urlopen") as uo:
            if raise_exc:
                uo.side_effect = raise_exc
            else:
                uo.return_value = FakeResp(payload)
            return judges_mod.eval_one(make_judge(), "KEY", "g", ["c"], [], "tx")

    def test_missing_key(self):
        r = judges_mod.eval_one(make_judge(), "", "g", ["c"], [], "tx")
        self.assertIn("missing API key", r["error"])

    def test_bad_format(self):
        r = judges_mod.eval_one(make_judge(fmt="x"), "K", "g", ["c"], [], "tx")
        self.assertIn("unknown judge format", r["error"])

    def test_success_complete(self):
        r = self._eval(openai_payload(verdict_json("complete", 0.9)))
        self.assertEqual(r["verdict"], "complete")
        self.assertIsNone(r["error"])

    def test_success_incomplete_with_evidence(self):
        r = self._eval(openai_payload(verdict_json("incomplete", 0.8, "tests fail")))
        self.assertEqual(r["blocking_evidence"], "tests fail")

    def test_request_failure(self):
        r = self._eval(raise_exc=urllib.error.URLError("down"))
        self.assertIn("request failed", r["error"])

    def test_bad_response_shape(self):
        r = self._eval({"unexpected": True})  # KeyError in parse_response
        self.assertIn("bad response", r["error"])

    def test_unparseable_verdict(self):
        r = self._eval(openai_payload("not json at all"))
        self.assertEqual(r["error"], "unparseable verdict")

    def test_verdict_without_verdict_key(self):
        r = self._eval(openai_payload('{"confidence":0.5}'))
        self.assertEqual(r["error"], "unparseable verdict")

    def test_confidence_non_numeric_defaults_zero(self):
        bad = json.dumps({"verdict": "incomplete", "confidence": "high",
                          "blocking_evidence": "x"})
        r = self._eval(openai_payload(bad))
        self.assertEqual(r["confidence"], 0.0)

    def test_criteria_not_list(self):
        bad = json.dumps({"verdict": "complete", "confidence": 1.0,
                          "criteria": "nope", "blocking_evidence": ""})
        r = self._eval(openai_payload(bad))
        self.assertEqual(r["criteria"], [])


# ── judges: evaluate + aggregate ──────────────────────────────────────────────

class TestEvaluateAggregate(unittest.TestCase):
    def test_evaluate_parallel(self):
        js = [make_judge("a"), make_judge("b")]
        with mock.patch.object(judges_mod.urllib.request, "urlopen") as uo:
            uo.return_value = FakeResp(openai_payload(verdict_json("complete", 0.9)))
            results, agg = judges_mod.evaluate(
                js, lambda n: "KEY", "g", ["c"], [], "tx")
        self.assertEqual(len(results), 2)
        self.assertEqual(agg["decision"], "complete")

    def test_evaluate_no_active(self):
        results, agg = judges_mod.evaluate([], lambda n: "K", "g", ["c"], [], "tx")
        self.assertEqual(results, [])
        self.assertEqual(agg["decision"], "error")

    def test_aggregate_error_when_all_failed(self):
        res = [{"id": "a", "verdict": None, "error": "boom"}]
        self.assertEqual(judges_mod.aggregate([make_judge("a")], res)["decision"], "error")

    def test_aggregate_block_on_evidence(self):
        res = [{"id": "a", "verdict": "incomplete", "confidence": 0.9,
                "blocking_evidence": "test_x fails", "error": None}]
        agg = judges_mod.aggregate([make_judge("a")], res)
        self.assertEqual(agg["decision"], "block")
        self.assertIn("test_x fails", agg["reason"])

    def test_aggregate_no_block_without_evidence(self):
        res = [{"id": "a", "verdict": "incomplete", "confidence": 0.9,
                "blocking_evidence": "", "error": None}]
        self.assertEqual(judges_mod.aggregate([make_judge("a")], res)["decision"], "complete")

    def test_aggregate_no_block_below_threshold(self):
        res = [{"id": "a", "verdict": "incomplete", "confidence": 0.2,
                "blocking_evidence": "weak", "error": None}]
        self.assertEqual(
            judges_mod.aggregate([make_judge("a", threshold=0.5)], res)["decision"],
            "complete")

    def test_aggregate_complete(self):
        res = [{"id": "a", "verdict": "complete", "confidence": 1.0,
                "blocking_evidence": "", "error": None}]
        self.assertEqual(judges_mod.aggregate([make_judge("a")], res)["decision"], "complete")

    def test_threshold_default_for_unknown_id(self):
        self.assertEqual(judges_mod._threshold_for([], "nope"), judges_mod.DEFAULT_THRESHOLD)

    def test_build_reason_dedupes(self):
        reason = judges_mod.build_reason(["x", "x", "y", ""])
        self.assertEqual(reason.count("1."), 1)
        self.assertIn("1. x", reason)
        self.assertIn("2. y", reason)


# ── gate: full algorithm ──────────────────────────────────────────────────────

class TestGate(unittest.TestCase):
    def _arm(self, cwd, **over):
        s = common.default_state()
        s.update({"active": True, "mode": "goal", "goal": "g", "criteria": ["c"],
                  "checks": [], "started_at": time.time()})
        s.update(over)
        common.save_state(cwd, s)
        return s

    def _judges(self, cwd):
        judges_mod.save_judges(cwd, [make_judge()])

    def _input(self, cwd, transcript=None):
        return {"cwd": cwd, "transcript_path": transcript}

    def test_not_armed_allows(self):
        with tempfile.TemporaryDirectory() as cwd:
            self.assertEqual(gate.run_stop_gate(self._input(cwd), env={})["action"], "allow")

    def test_infer_no_transcript_allows(self):
        with tempfile.TemporaryDirectory() as cwd:
            env = {"CLAUDE_PLUGIN_OPTION_VERIFY_WITHOUT_GOAL": "true"}
            self.assertEqual(gate.run_stop_gate(self._input(cwd), env=env)["action"], "allow")

    def test_infer_no_request_allows(self):
        with tempfile.TemporaryDirectory() as cwd:
            tp = write_transcript(cwd, [{"role": "assistant",
                                         "content": [{"type": "text", "text": "hi"}]}])
            env = {"CLAUDE_PLUGIN_OPTION_VERIFY_WITHOUT_GOAL": "true"}
            self.assertEqual(
                gate.run_stop_gate(self._input(cwd, tp), env=env)["action"], "allow")

    def test_infer_arms_then_no_judges_allows(self):
        with tempfile.TemporaryDirectory() as cwd:
            tp = write_transcript(cwd, [{"role": "user",
                                         "content": [{"type": "text", "text": "do the thing"}]}])
            env = {"CLAUDE_PLUGIN_OPTION_VERIFY_WITHOUT_GOAL": "true"}
            out = gate.run_stop_gate(self._input(cwd, tp), env=env)
            self.assertEqual(out["action"], "allow")
            self.assertEqual(common.read_report(cwd)["status"], "no_judges")

    def test_no_judges_disarms(self):
        with tempfile.TemporaryDirectory() as cwd:
            self._arm(cwd)
            out = gate.run_stop_gate(self._input(cwd), env={})
            self.assertEqual(out["action"], "allow")
            self.assertEqual(common.read_report(cwd)["status"], "no_judges")

    def test_max_iterations(self):
        with tempfile.TemporaryDirectory() as cwd:
            self._arm(cwd, iteration=99)
            self._judges(cwd)
            out = gate.run_stop_gate(self._input(cwd), env={})
            self.assertEqual(out["action"], "allow")
            self.assertEqual(common.read_report(cwd)["status"], "max_iterations")

    def test_wall_clock(self):
        with tempfile.TemporaryDirectory() as cwd:
            self._arm(cwd, started_at=time.time() - 10 ** 9)
            self._judges(cwd)
            out = gate.run_stop_gate(self._input(cwd), env={})
            self.assertEqual(out["action"], "allow")
            self.assertEqual(common.read_report(cwd)["status"], "timeout")

    def test_stuck(self):
        with tempfile.TemporaryDirectory() as cwd:
            h = common.evidence_hash([], 0)
            self._arm(cwd, last_evidence_hash=h)
            self._judges(cwd)
            env = {"CLAUDE_PLUGIN_OPTION_STUCK_LIMIT": "1"}
            out = gate.run_stop_gate(self._input(cwd), env=env)
            self.assertEqual(out["action"], "allow")
            self.assertEqual(common.read_report(cwd)["status"], "stuck")

    def test_complete_allows(self):
        with tempfile.TemporaryDirectory() as cwd:
            self._arm(cwd)
            self._judges(cwd)
            env = {"OPENAI_API_KEY": "KEY"}
            with mock.patch.object(judges_mod.urllib.request, "urlopen") as uo, \
                 mock.patch.object(common.subprocess, "run",
                                   return_value=mock.Mock(returncode=0, stdout="", stderr="")):
                uo.return_value = FakeResp(openai_payload(verdict_json("complete", 0.9)))
                out = gate.run_stop_gate(self._input(cwd), env=env)
            self.assertEqual(out["action"], "allow")
            self.assertEqual(common.read_report(cwd)["status"], "complete")

    def test_block_continues(self):
        with tempfile.TemporaryDirectory() as cwd:
            self._arm(cwd)
            self._judges(cwd)
            env = {"OPENAI_API_KEY": "KEY"}
            with mock.patch.object(judges_mod.urllib.request, "urlopen") as uo, \
                 mock.patch.object(common.subprocess, "run",
                                   return_value=mock.Mock(returncode=1, stdout="FAIL", stderr="")):
                uo.return_value = FakeResp(
                    openai_payload(verdict_json("incomplete", 0.9, "test_login fails")))
                out = gate.run_stop_gate(self._input(cwd), env=env)
            self.assertEqual(out["action"], "block")
            self.assertIn("test_login fails", out["reason"])
            st = common.load_state(cwd)
            self.assertEqual(st["iteration"], 1)
            self.assertEqual(st["last_reason"], out["reason"])

    def test_judge_error_allows(self):
        with tempfile.TemporaryDirectory() as cwd:
            self._arm(cwd)
            self._judges(cwd)
            env = {"OPENAI_API_KEY": "KEY"}
            with mock.patch.object(judges_mod.urllib.request, "urlopen",
                                   side_effect=urllib.error.URLError("down")):
                out = gate.run_stop_gate(self._input(cwd), env=env)
            self.assertEqual(out["action"], "allow")
            self.assertEqual(common.read_report(cwd)["status"], "judge_error")

    def test_subagent_phase_logged(self):
        with tempfile.TemporaryDirectory() as cwd:
            self._arm(cwd)
            self._judges(cwd)
            env = {"OPENAI_API_KEY": "KEY"}
            with mock.patch.object(judges_mod.urllib.request, "urlopen") as uo, \
                 mock.patch.object(common.subprocess, "run",
                                   return_value=mock.Mock(returncode=0, stdout="", stderr="")):
                uo.return_value = FakeResp(openai_payload(verdict_json("complete", 0.9)))
                gate.run_stop_gate(self._input(cwd), env=env, subagent=True)
            log = Path(common.decisions_path(cwd)).read_text()
            self.assertIn("subagent_stop", log)

    def test_default_env_uses_os_environ(self):
        with tempfile.TemporaryDirectory() as cwd:
            with mock.patch.dict(os.environ, {}, clear=False):
                self.assertEqual(
                    gate.run_stop_gate({"cwd": cwd, "transcript_path": None})["action"],
                    "allow")

    def test_cwd_defaults_when_absent(self):
        with mock.patch.object(gate.os, "getcwd", return_value=tempfile.mkdtemp()):
            self.assertEqual(gate.run_stop_gate({}, env={})["action"], "allow")


# ── manage CLI ────────────────────────────────────────────────────────────────

class TestManage(unittest.TestCase):
    def _run(self, cwd, args):
        out = io.StringIO()
        rc = manage.main(["manage.py"] + args, {"TRUSTED_LOOP_CWD": cwd}, out)
        return rc, out.getvalue()

    def test_no_args(self):
        rc, _ = self._run("/tmp", [])
        self.assertEqual(rc, 1)

    def test_unknown_command(self):
        rc, _ = self._run("/tmp", ["frobnicate"])
        self.assertEqual(rc, 1)

    def test_set_goal_and_status(self):
        with tempfile.TemporaryDirectory() as cwd:
            rc, out = self._run(cwd, ["set-goal", "make green",
                                      "--criteria", "tests pass", "--checks", "pytest"])
            self.assertEqual(rc, 0)
            self.assertIn("armed", out)
            rc, out = self._run(cwd, ["status"])
            self.assertIn("make green", out)
            self.assertIn("pytest", out)

    def test_set_goal_requires_goal(self):
        with tempfile.TemporaryDirectory() as cwd:
            rc, _ = self._run(cwd, ["set-goal", "--criteria", "x"])
            self.assertEqual(rc, 1)

    def test_set_goal_default_criteria(self):
        with tempfile.TemporaryDirectory() as cwd:
            self._run(cwd, ["set-goal", "just do it"])
            self.assertEqual(common.load_state(cwd)["criteria"],
                             ["fully satisfy the stated goal"])

    def test_clear_goal_when_armed(self):
        with tempfile.TemporaryDirectory() as cwd:
            self._run(cwd, ["set-goal", "g"])
            rc, out = self._run(cwd, ["clear-goal"])
            self.assertIn("disarmed", out)
            self.assertFalse(common.load_state(cwd)["active"])

    def test_clear_goal_when_not_armed(self):
        with tempfile.TemporaryDirectory() as cwd:
            rc, out = self._run(cwd, ["clear-goal"])
            self.assertIn("not armed", out)

    def test_status_shows_last_reason(self):
        with tempfile.TemporaryDirectory() as cwd:
            s = common.default_state()
            s.update({"active": True, "last_reason": "fix the bug"})
            common.save_state(cwd, s)
            rc, out = self._run(cwd, ["status"])
            self.assertIn("fix the bug", out)

    def test_judges_add_list_remove(self):
        with tempfile.TemporaryDirectory() as cwd:
            rc, out = self._run(cwd, ["judges-add", "--id", "oai", "--format", "openai",
                                      "--endpoint", "https://x/v1", "--model", "gpt",
                                      "--key-env", "OPENAI_API_KEY"])
            self.assertEqual(rc, 0)
            rc, out = self._run(cwd, ["judges-list"])
            self.assertIn("oai", out)
            rc, out = self._run(cwd, ["judges-remove", "--id", "oai"])
            self.assertIn("Removed", out)
            rc, out = self._run(cwd, ["judges-list"])
            self.assertIn("No judges", out)

    def test_judges_add_overwrites_same_id(self):
        with tempfile.TemporaryDirectory() as cwd:
            base = ["judges-add", "--id", "j", "--format", "openai",
                    "--endpoint", "https://x/v1", "--model", "gpt", "--key-env", "K"]
            self._run(cwd, base)
            self._run(cwd, base)
            self.assertEqual(len(judges_mod.load_judges(cwd)), 1)

    def test_judges_add_missing_required(self):
        with tempfile.TemporaryDirectory() as cwd:
            rc, _ = self._run(cwd, ["judges-add", "--id", "j"])
            self.assertEqual(rc, 1)

    def test_judges_add_bad_format(self):
        with tempfile.TemporaryDirectory() as cwd:
            rc, _ = self._run(cwd, ["judges-add", "--id", "j", "--format", "grok",
                                    "--endpoint", "u", "--model", "m", "--key-env", "K"])
            self.assertEqual(rc, 1)

    def test_judges_add_bad_number(self):
        with tempfile.TemporaryDirectory() as cwd:
            rc, _ = self._run(cwd, ["judges-add", "--id", "j", "--format", "openai",
                                    "--endpoint", "u", "--model", "m", "--key-env", "K",
                                    "--temperature", "hot"])
            self.assertEqual(rc, 1)

    def test_judges_add_flag_without_value(self):
        with tempfile.TemporaryDirectory() as cwd:
            rc, _ = self._run(cwd, ["judges-add", "--id"])
            self.assertEqual(rc, 1)

    def test_judges_remove_requires_id(self):
        with tempfile.TemporaryDirectory() as cwd:
            rc, _ = self._run(cwd, ["judges-remove"])
            self.assertEqual(rc, 1)

    def test_judges_remove_flag_without_value(self):
        with tempfile.TemporaryDirectory() as cwd:
            rc, _ = self._run(cwd, ["judges-remove", "--id"])
            self.assertEqual(rc, 1)

    def test_judges_remove_unknown_id(self):
        with tempfile.TemporaryDirectory() as cwd:
            rc, out = self._run(cwd, ["judges-remove", "--id", "ghost"])
            self.assertIn("No judge", out)

    def test_collect_flag_without_value(self):
        args = ["set-goal", "g", "--criteria"]
        with self.assertRaises(ValueError):
            manage._collect(args, "--criteria")

    def test_resolve_cwd_precedence(self):
        self.assertEqual(manage._resolve_cwd({"TRUSTED_LOOP_CWD": "/a"}), "/a")
        self.assertEqual(manage._resolve_cwd({"CLAUDE_CWD": "/b"}), "/b")

    def test_default_streams(self):
        with tempfile.TemporaryDirectory() as cwd:
            with mock.patch.dict(os.environ, {"TRUSTED_LOOP_CWD": cwd}):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = manage.main(["manage.py", "status"])
                self.assertEqual(rc, 0)


# ── adapters: on_stop / on_precompact / on_session_start ──────────────────────

class TestOnStop(unittest.TestCase):
    def test_block_prints_decision(self):
        with mock.patch.object(on_stop.gate, "run_stop_gate",
                               return_value={"action": "block", "reason": "do x"}):
            out = call_main(on_stop, '{"cwd":"/tmp"}')
        self.assertEqual(json.loads(out)["decision"], "block")
        self.assertEqual(json.loads(out)["reason"], "do x")

    def test_allow_prints_nothing(self):
        with mock.patch.object(on_stop.gate, "run_stop_gate", return_value={"action": "allow"}):
            self.assertEqual(call_main(on_stop, '{"cwd":"/tmp"}').strip(), "")

    def test_subagent_flag_forwarded(self):
        rec = mock.Mock(return_value={"action": "allow"})
        with mock.patch.object(on_stop.gate, "run_stop_gate", rec):
            call_main(on_stop, "{}", argv=["on_stop.py", "--subagent"])
        self.assertTrue(rec.call_args.kwargs["subagent"])

    def test_garbage_stdin(self):
        self.assertEqual(call_main(on_stop, "not json").strip(), "")

    def test_empty_stdin(self):
        with mock.patch.object(on_stop.gate, "run_stop_gate", return_value={"action": "allow"}):
            self.assertEqual(call_main(on_stop, "").strip(), "")

    def test_gate_exception_fails_open(self):
        with mock.patch.object(on_stop.gate, "run_stop_gate", side_effect=RuntimeError):
            self.assertEqual(call_main(on_stop, "{}").strip(), "")


class TestOnPrecompact(unittest.TestCase):
    def test_writes_checkpoint_when_active(self):
        with tempfile.TemporaryDirectory() as cwd:
            s = common.default_state()
            s.update({"active": True, "goal": "g"})
            common.save_state(cwd, s)
            call_main(on_precompact, json.dumps({"cwd": cwd}))
            self.assertEqual(common.read_checkpoint(cwd)["goal"], "g")

    def test_no_checkpoint_when_inactive(self):
        with tempfile.TemporaryDirectory() as cwd:
            call_main(on_precompact, json.dumps({"cwd": cwd}))
            self.assertIsNone(common.read_checkpoint(cwd))

    def test_garbage_stdin(self):
        self.assertEqual(call_main(on_precompact, "nope").strip(), "")

    def test_inner_exception_suppressed(self):
        with mock.patch.object(on_precompact.common, "load_state", side_effect=RuntimeError):
            call_main(on_precompact, '{"cwd":"/tmp"}')  # must not raise


class TestOnSessionStart(unittest.TestCase):
    def test_injects_briefing_when_active(self):
        with tempfile.TemporaryDirectory() as cwd:
            s = common.default_state()
            s.update({"active": True, "goal": "make green"})
            common.save_state(cwd, s)
            out = call_main(on_session_start, json.dumps({"cwd": cwd, "source": "compact"}))
            payload = json.loads(out)
            self.assertEqual(payload["hookSpecificOutput"]["hookEventName"], "SessionStart")
            self.assertIn("make green", payload["hookSpecificOutput"]["additionalContext"])

    def test_no_output_when_nothing_to_say(self):
        with tempfile.TemporaryDirectory() as cwd:
            self.assertEqual(
                call_main(on_session_start, json.dumps({"cwd": cwd})).strip(), "")

    def test_garbage_stdin(self):
        self.assertEqual(call_main(on_session_start, "nope").strip(), "")

    def test_inner_exception_suppressed(self):
        with mock.patch.object(on_session_start.common, "session_briefing",
                               side_effect=RuntimeError):
            self.assertEqual(call_main(on_session_start, '{"cwd":"/tmp"}').strip(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
