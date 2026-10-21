"""The ⟳ Restore list across a tmux crash (restore.py).

The unit tests drive restore.py's pure functions with made-up server ids. The crash test runs a
real tmux server on a private socket (`tmux -L <name>`), so it never touches the sessions of the
machine it runs on; it is skipped where tmux isn't installed.

    python -m unittest discover -s tests
"""
import os
import shutil
import subprocess
import sys
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import restore  # noqa: E402


def agent(name):
    return {"dir": "/work/" + name, "group": "personal", "sid": str(uuid.uuid5(uuid.NAMESPACE_DNS, name))}


def alive(*names):
    return {n: agent(n) for n in names}


class Merge(unittest.TestCase):
    def test_same_server_tracks_the_live_set(self):
        st = restore.merge({}, alive("a", "b", "c"), "S1")
        st = restore.merge(st, alive("a", "b"), "S1")              # c closed by hand under the same server
        self.assertEqual(set(st["sessions"]), {"a", "b"})
        self.assertEqual(restore.candidates(st, {"a", "b"}), {})

    def test_tmux_gone_keeps_everything_on_offer(self):
        st = restore.merge({}, alive("a", "b", "c"), "S1")
        st = restore.merge(st, {}, "")                             # no server, nothing alive: nothing written
        self.assertEqual(set(restore.candidates(st, set())), {"a", "b", "c"})

    def test_partial_restore_after_a_crash_keeps_the_rest(self):
        st = restore.merge({}, alive("a", "b", "c"), "S1")
        st = restore.merge(st, alive("a"), "S2")                   # new server, a restored by hand
        self.assertEqual(set(st["sessions"]), {"a"})
        self.assertEqual(set(st["baseline"]), {"b", "c"})
        self.assertEqual(set(restore.candidates(st, {"a"})), {"b", "c"})
        st = restore.merge(st, alive("a", "b"), "S2")              # b comes back too
        self.assertEqual(set(restore.candidates(st, {"a", "b"})), {"c"})
        st = restore.merge(st, alive("a", "b", "c"), "S2")
        self.assertEqual(st["baseline"], {})

    def test_a_fresh_session_first_does_not_wipe_the_list(self):
        st = restore.merge({}, alive("a", "b"), "S1")
        st = restore.merge(st, alive("new"), "S2")
        self.assertEqual(set(restore.candidates(st, {"new"})), {"a", "b"})

    def test_two_crashes_before_restoring_accumulate(self):
        st = restore.merge({}, alive("a", "b", "c"), "S1")
        st = restore.merge(st, alive("a"), "S2")
        st = restore.merge(st, alive("d"), "S3")                   # crashed again, a not yet restored
        self.assertEqual(set(restore.candidates(st, {"d"})), {"a", "b", "c"})

    def test_legacy_file_without_server_id(self):
        old = {"ts": 1, "sessions": alive("a", "b")}               # live.json before the baseline existed
        self.assertEqual(set(restore.candidates(old, set())), {"a", "b"})
        st = restore.merge(old, alive("a"), "S1")                  # first poll after the upgrade: no known server to compare
        self.assertEqual(st["server"], "S1")

    def test_agents_without_a_conversation_are_not_offered(self):
        st = restore.merge({}, {"a": agent("a"), "b": {"dir": "/work/b", "group": "personal", "sid": ""}}, "S1")
        self.assertEqual(set(restore.candidates(st, set())), {"a"})


class DeliberateClose(unittest.TestCase):
    def setUp(self):
        st = restore.merge({}, alive("a", "b", "c"), "S1")
        self.st = restore.merge(st, alive("a"), "S2")              # b and c waiting in the baseline

    def test_killed_through_the_office_never_comes_back(self):
        st = restore.drop(self.st, "b")
        self.assertEqual(set(restore.candidates(st, {"a"})), {"c"})
        st = restore.drop(st, "a")                                 # a live one closed on purpose
        self.assertEqual(set(restore.candidates(st, set())), {"c"})

    def test_rename_carries_over(self):
        st = restore.rename(self.st, "c", "c2")
        self.assertEqual(set(restore.candidates(st, {"a"})), {"b", "c2"})

    def test_forget_clears_everything_not_running(self):
        st = restore.forget(self.st, {"a"})
        self.assertEqual(restore.candidates(st, {"a"}), {})
        self.assertEqual(set(st["sessions"]), {"a"})


@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class CrashOnARealTmuxServer(unittest.TestCase):
    """Start agents, kill the whole tmux server, restore one by hand: the rest stay on offer."""

    def setUp(self):
        self.sock = "office-test-" + uuid.uuid4().hex[:8]

    def tearDown(self):
        subprocess.run(["tmux", "-L", self.sock, "kill-server"], capture_output=True)

    def tmux(self, *args):
        return subprocess.run(["tmux", "-L", self.sock, *args], capture_output=True, text=True)

    def start(self, name):
        self.tmux("new-session", "-d", "-s", name, "sleep 600")

    def poll(self, st):
        # what server.record_live does on each floor poll, against this private server
        names = [n for n in self.tmux("list-sessions", "-F", "#{session_name}").stdout.split() if n]
        cur = {n: agent(n) for n in names}
        return (restore.merge(st, cur, restore.server_id(self.sock)) if cur else st), set(names)

    def test_banner_keeps_the_rest_after_a_partial_restore(self):
        for n in ("ana", "ben", "cai", "dov"):
            self.start(n)
        first = restore.server_id(self.sock)
        self.assertTrue(first)
        st, live = self.poll({})
        self.assertEqual(restore.candidates(st, live), {})

        self.tmux("kill-server")                                   # the crash
        self.assertEqual(restore.server_id(self.sock), "")
        st, live = self.poll(st)
        self.assertEqual(set(restore.candidates(st, live)), {"ana", "ben", "cai", "dov"})

        self.start("ben")                                          # restored by hand: a new server
        self.assertNotEqual(restore.server_id(self.sock), first)
        st, live = self.poll(st)
        self.assertEqual(set(restore.candidates(st, live)), {"ana", "cai", "dov"})

        self.start("eve")                                          # a brand-new agent changes nothing
        st, live = self.poll(st)
        self.assertEqual(set(restore.candidates(st, live)), {"ana", "cai", "dov"})

        for n in ("ana", "cai", "dov"):                            # the Restore button does the rest
            self.start(n)
        st, live = self.poll(st)
        self.assertEqual(restore.candidates(st, live), {})
        self.assertEqual(st["baseline"], {})


if __name__ == "__main__":
    unittest.main()
