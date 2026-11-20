"""Phone pushes (server.notify_changes): one per question or finish, and none for an agent whose
chat you are looking at right now. push() is faked, so nothing reaches Apple."""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402


def desk(state, ask="", since=0):
    return {"session": "zed-demo", "state": state, "ask": ask, "gist": "all good", "muted": False, "since": since}


class Notify(unittest.TestCase):
    def setUp(self):
        server._NOTIFIED.clear(); server._LOOKING.clear()
        server._NOTIFY_SEEDED = True
        self.sent = []
        patches = [mock.patch.object(server, "push", lambda s, body, **k: self.sent.append(body)),
                   mock.patch.object(type(server.PUSH), "ready", new_callable=mock.PropertyMock, return_value=True),
                   mock.patch.object(server, "load_mutes", lambda: set()),
                   mock.patch.object(server.PUSH, "_log", lambda line: None)]
        for p in patches:
            p.start(); self.addCleanup(p.stop)

    def finish(self):
        server.notify_changes([desk("working")])
        server.notify_changes([desk("review")])

    def test_a_finish_pushes_once(self):
        self.finish()
        server.notify_changes([desk("review")])
        self.assertEqual(self.sent, ["done: all good"])

    def test_no_push_while_its_chat_is_in_front_of_you(self):
        server._LOOKING["page1"] = ("zed-demo", time.time())
        self.finish()
        server.notify_changes([desk("working")])
        server.notify_changes([desk("waiting", "Which one?")])
        self.assertEqual(self.sent, [])

    def test_looking_at_another_chat_or_a_stale_page_still_pushes(self):
        server._LOOKING["page1"] = ("amy-other", time.time())
        server._LOOKING["page2"] = ("zed-demo", time.time() - server.LOOK_FOR - 1)
        self.finish()
        self.assertEqual(self.sent, ["done: all good"])

    def test_what_you_saw_is_not_pushed_after_you_leave(self):
        server._LOOKING["page1"] = ("zed-demo", time.time())
        self.finish()
        server._LOOKING.clear()
        server.notify_changes([desk("review")])
        self.assertEqual(self.sent, [])

    def test_a_question_that_comes_after_you_stopped_looking_still_pushes(self):
        # you locked the phone; its last word was 3 s ago, still within LOOK_FOR, but the question is newer
        server._LOOKING["page1"] = ("zed-demo", time.time() - 3)
        server.notify_changes([desk("working")])
        server.notify_changes([desk("waiting", "Which one?", since=time.time())])
        self.assertEqual(self.sent, ["needs you: Which one?"])

    def test_a_change_while_you_look_is_quiet_even_between_two_heartbeats(self):
        t = time.time() - 0.8
        server._LOOKING["page1"] = ("zed-demo", t)
        server.notify_changes([desk("working")])
        server.notify_changes([desk("review", since=t + 0.7)])
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
