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


def _line(kind, ts, text, cwd="/home/user/project"):
    """One transcript JSONL record in the shape team.py parses."""
    return json.dumps({
        "type": kind,
        "cwd": cwd,
        "timestamp": ts,
        "message": {"content": [{"type": "text", "text": text}]},
    })


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


class RealPrompt(unittest.TestCase):
    def test_plain_text_is_real(self):
        self.assertTrue(team._is_real_prompt("please refactor the parser"))

    def test_injected_blocks_are_not(self):
        self.assertFalse(team._is_real_prompt("<command-name>/review</command-name>"))
        self.assertFalse(team._is_real_prompt("Caveat: this session is odd"))
        self.assertFalse(team._is_real_prompt(""))


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


if __name__ == "__main__":
    unittest.main()
