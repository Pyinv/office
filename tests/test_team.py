"""Unit tests for the transcript/session detector (team.py).

Pure-stdlib, no third-party deps and no tmux required: the detector's helpers
are exercised directly against synthetic transcripts written to a temp dir, so
the suite carries no real session data and runs anywhere `python` does.

    python -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import team  # noqa: E402


def _line(kind, ts, text, cwd="/home/user/project", origin="human", mid=None):
    """One transcript JSONL record in the shape team.py parses (a user line is one you typed)."""
    rec = {"type": kind, "cwd": cwd, "timestamp": ts,
           "message": {"content": [{"type": "text", "text": text}]}}
    if kind == "user" and origin:
        rec["origin"] = {"kind": origin}
    if mid:
        rec["message"]["id"] = mid
    return json.dumps(rec)


def _write(path, records, mtime=None):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(r + "\n")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


class SplitName(unittest.TestCase):
    def test_person_and_project(self):
        self.assertEqual(team.split_name("alice-widgets"), ("Alice", "widgets"))

    def test_project_keeps_inner_dashes(self):
        # only the FIRST dash splits person from project
        self.assertEqual(team.split_name("carol-data-pipeline"), ("Carol", "data-pipeline"))

    def test_no_dash_is_project_only(self):
        self.assertEqual(team.split_name("standalone"), ("", "standalone"))


class Epoch(unittest.TestCase):
    def test_zulu_parsed_and_ordered(self):
        a = team._epoch("2026-01-02T03:04:05Z")
        b = team._epoch("2026-01-02T03:04:06Z")
        self.assertGreater(a, 0)
        self.assertEqual(b - a, 1)          # one second apart, parsed as UTC

    def test_naive_assumed_utc(self):
        self.assertEqual(
            team._epoch("2026-01-02T03:04:05"),
            team._epoch("2026-01-02T03:04:05Z"),
        )

    def test_empty_is_zero(self):
        self.assertEqual(team._epoch(None), 0)
        self.assertEqual(team._epoch(""), 0)


class Yours(unittest.TestCase):
    def test_the_transcript_marks_what_you_typed(self):
        self.assertTrue(team._is_yours(json.loads(_line("user", "", "please refactor the parser"))))

    def test_everything_else_is_not(self):
        for origin in ("task-notification", "peer", None):
            self.assertFalse(team._is_yours(json.loads(_line("user", "", "<command-name>/review", origin=origin))))
        self.assertFalse(team._is_yours(json.loads(_line("assistant", "", "hi"))))


class AskOf(unittest.TestCase):
    """What a waiting agent asks: from Claude Code's status file and the hook's file, never its words."""
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self._old, team.ASK_DIR = team.ASK_DIR, self.d.name
        self.now = int(time.time() * 1000)

    def tearDown(self):
        team.ASK_DIR = self._old
        self.d.cleanup()

    def _hook(self, sid, event, name, inp):
        with open(os.path.join(self.d.name, sid + ".json"), "w") as f:
            json.dump({"event": event, "tool_name": name, "tool_input": inp}, f)

    def _meta(self, sid, why, at=None):
        return {"sessionId": sid, "status": "waiting", "waitingFor": why, "statusUpdatedAt": at or self.now}

    def test_a_question_with_its_choices(self):
        self._hook("s1", "PreToolUse", "AskUserQuestion", {"questions": [
            {"question": "Which room?", "options": [{"label": "Ventures"}, {"label": "Clients"}]}, {"question": "And?"}]})
        self.assertEqual(team._ask_of(self._meta("s1", "input needed")),
                         {"kind": "question", "ask": "Which room?", "options": ["Ventures", "Clients"], "more": 1, "detail": ""})

    def test_a_permission_with_what_it_would_run(self):
        self._hook("s2", "PermissionRequest", "Bash", {"command": "rm -rf build", "description": "clean"})
        a = team._ask_of(self._meta("s2", "permission prompt"))
        self.assertEqual((a["kind"], a["ask"], a["detail"]), ("permission", "Permission to use Bash", "rm -rf build"))

    def test_an_old_file_or_none_is_only_the_wait_itself(self):
        self._hook("s3", "PreToolUse", "AskUserQuestion", {"questions": [{"question": "old?"}]})
        later = self.now + 10 * 60 * 1000
        self.assertEqual(team._ask_of(self._meta("s3", "input needed", at=later))["kind"], "dialog")
        self.assertEqual(team._ask_of(self._meta("nofile", "dialog open"))["ask"], "Dialog open")


class Turns(unittest.TestCase):
    def test_every_text_of_a_turn_in_order_and_the_last_n_turns(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "a.jsonl")
            _write(f, [
                _line("user", "2026-07-01T10:00:00Z", "first"),
                _line("assistant", "2026-07-01T10:00:01Z", "ok one"),
                _line("user", "2026-07-01T10:01:00Z", "skill text", origin=None),     # not yours: same turn
                _line("user", "2026-07-01T10:02:00Z", "second"),
                _line("assistant", "2026-07-01T10:02:01Z", "looking", mid="m1"),
                _line("assistant", "2026-07-01T10:02:02Z", "still the same message", mid="m1"),
                _line("assistant", "2026-07-01T10:02:09Z", "done", mid="m2"),
            ])
            t = team.turns(f)
            self.assertEqual([x["you"] for x in t], ["first", "second"])
            self.assertEqual(t[0]["said"], ["ok one"])
            self.assertEqual(t[1]["said"], ["looking\n\nstill the same message", "done"])
            self.assertEqual([x["you"] for x in team.turns(f, n=1)], ["second"])

    def test_long_replies_dont_cut_the_history_short(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "a.jsonl")
            rows = []
            for i in range(12):
                rows += [_line("user", f"2026-07-01T10:{i:02d}:00Z", f"ask {i}"),
                         _line("assistant", f"2026-07-01T10:{i:02d}:05Z", "x" * 3000)]
            _write(f, rows)
            self.assertEqual([x["you"] for x in team.turns(f, n=10, kb=4)], [f"ask {i}" for i in range(2, 12)])


class ParseLatest(unittest.TestCase):
    def test_single_transcript(self):
        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "a.jsonl"), [
                _line("user", "2026-07-01T10:00:00Z", "start the report"),
                _line("assistant", "2026-07-01T10:00:05Z", "here is the report"),
            ])
            rec = team._parse_latest(d)
            self.assertEqual(rec["asst"], "here is the report")
            self.assertEqual(rec["asst_ts"], "2026-07-01T10:00:05Z")

    def test_prefers_newest_message_not_file_mtime(self):
        # Regression: a resumed/idle conversation's file can have its mtime
        # bumped (title/agent-name/resume metadata) ABOVE the file you're
        # actually working in, while its last real message stays days old.
        # _parse_latest must select by last MESSAGE time, not inode mtime, or
        # the wall reports "active 5 days ago" for a session in use today.
        now = time.time()
        with tempfile.TemporaryDirectory() as d:
            # active conversation: recent message, but an OLDER file mtime
            _write(os.path.join(d, "active.jsonl"), [
                _line("user", "2026-07-22T14:00:00Z", "keep going"),
                _line("assistant", "2026-07-22T14:20:00Z", "todays real answer"),
            ], mtime=now - 3600)          # touched an hour ago
            # stale conversation: 5-day-old message, but a NEWER file mtime
            _write(os.path.join(d, "stale.jsonl"), [
                _line("user", "2026-07-17T09:00:00Z", "wrap it up"),
                _line("assistant", "2026-07-17T09:44:00Z", "older archived reply"),
            ], mtime=now - 60)            # re-touched a minute ago (metadata write)

            rec = team._parse_latest(d)
            # must follow the newest MESSAGE, not the newest mtime
            self.assertEqual(rec["asst"], "todays real answer")
            self.assertEqual(rec["asst_ts"], "2026-07-22T14:20:00Z")
            self.assertEqual(rec["ts"], "2026-07-22T14:20:00Z")

    def test_ts_tracks_last_message_even_if_user(self):
        # A prompt sent while Claude is still working is the freshest activity:
        # `ts` should follow it, so an in-flight session doesn't sink on the wall.
        with tempfile.TemporaryDirectory() as d:
            _write(os.path.join(d, "a.jsonl"), [
                _line("assistant", "2026-07-01T10:00:00Z", "earlier reply"),
                _line("user", "2026-07-01T12:30:00Z", "now do the next thing"),
            ])
            rec = team._parse_latest(d)
            self.assertEqual(rec["ts"], "2026-07-01T12:30:00Z")     # last message (the prompt)
            self.assertEqual(rec["asst_ts"], "2026-07-01T10:00:00Z")  # last reply, unchanged

    def test_empty_dir_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(team._parse_latest(d))


class PromptBox(unittest.TestCase):
    """team._pane reads the agent's input box from a captured pane (with colours)."""

    PANE = ("● done.\n"
            "\x1b[2m" + "─" * 40 + "\x1b[0m\n"
            "{box}\n"
            "\x1b[2m" + "─" * 40 + "\x1b[0m\n"
            "  ~/proj\n  ⏵⏵ auto mode on (shift+tab to cycle)\n")

    def pane(self, box):
        orig = team._tmux
        team._tmux = lambda *a: self.PANE.format(box=box)
        try:
            return team._pane("x")
        finally:
            team._tmux = orig

    def test_claude_codes_dim_suggestion_is_not_a_draft(self):
        r = self.pane("\x1b[39m❯\xa0\x1b[2mput this example in html\x1b[0m")
        self.assertEqual(r[6], "")
        self.assertIsNone(r[1])

    def test_text_you_typed_is_a_draft(self):
        r = self.pane("\x1b[39m❯\xa0half typed message\x1b[0m")
        self.assertEqual(r[6], "half typed message")

    def test_empty_box(self):
        self.assertEqual(self.pane("❯\xa0")[6], "")


if __name__ == "__main__":
    unittest.main()
