"""Grouping phone shares into one message per agent (dropbatch.py), with a fake clock."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dropbatch import Batches, shared_text  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Rig(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.sent = []
        self.stuck = []
        self.accept = True
        self.b = Batches(self.deliver, on_stuck=lambda s, c: self.stuck.append((s, c)), now=self.clock)

    def deliver(self, session, msg):
        if not self.accept:
            return False
        self.sent.append((session, msg))
        return True

    def wait(self, secs):
        self.clock.t += secs
        self.b.tick()


class ExactCount(Rig):
    def test_item_n_of_n_delivers_at_once_in_order(self):
        self.b.add("ana", "text", "hello", "from whatsapp", i=1, n=3)
        self.b.add("ana", "file", "inbox/IMG_1.jpg", "from whatsapp", i=2, n=3)
        self.assertEqual(self.sent, [])
        self.assertTrue(self.b.add("ana", "text", "second\nline two", "from whatsapp", i=3, n=3))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][1],
                         '📎 shared from phone (3 items) — from whatsapp\n'
                         '1. "hello"\n2. inbox/IMG_1.jpg\n3. "second\n   line two"')

    def test_order_follows_the_index_not_arrival(self):
        self.b.add("ana", "file", "inbox/b.jpg", i=2, n=2)
        self.b.add("ana", "file", "inbox/a.jpg", i=1, n=2)
        self.assertIn("1. inbox/a.jpg\n2. inbox/b.jpg", self.sent[0][1])

    def test_slow_uploads_do_not_split_a_counted_batch(self):
        self.b.add("ana", "file", "inbox/1.jpg", i=1, n=2)
        self.wait(30)                                   # a big photo on slow mobile data
        self.assertEqual(self.sent, [])
        self.b.add("ana", "file", "inbox/2.jpg", i=2, n=2)
        self.assertEqual(len(self.sent), 1)

    def test_a_failed_upload_still_delivers_after_maxwait(self):
        self.b.add("ana", "file", "inbox/1.jpg", i=1, n=3)
        self.b.add("ana", "file", "inbox/2.jpg", i=2, n=3)
        self.wait(59)
        self.assertEqual(self.sent, [])
        self.wait(2)
        self.assertIn("(2 items)", self.sent[0][1])


class QuietFallback(Rig):
    def test_old_shortcut_groups_by_quiet_time(self):
        self.b.add("ana", "file", "inbox/1.png")
        self.wait(2)
        self.b.add("ana", "file", "inbox/2.png")
        self.wait(4)
        self.assertEqual(self.sent, [])
        self.wait(2)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("(2 items)", self.sent[0][1])

    def test_single_share_reads_as_before(self):
        self.b.add("ana", "file", "inbox/shot.png", "look at this")
        self.wait(6)
        self.assertEqual(self.sent, [("ana", "📎 shared from phone: inbox/shot.png  — look at this")])

    def test_single_text_reads_as_before(self):
        self.b.add("ana", "text", "https://example.com", n=1, i=1)
        self.assertEqual(self.sent, [("ana", "📝 shared from phone: https://example.com")])

    def test_agents_are_batched_separately(self):
        self.b.add("ana", "file", "inbox/a.png", i=1, n=1)
        self.b.add("ben", "file", "inbox/b.png")
        self.assertEqual([s for s, _ in self.sent], ["ana"])
        self.wait(6)
        self.assertEqual([s for s, _ in self.sent], ["ana", "ben"])


class Refused(Rig):
    def test_waits_while_the_agent_is_busy_then_delivers(self):
        self.accept = False
        self.b.add("ana", "file", "inbox/a.png", i=1, n=1)
        self.wait(1); self.wait(1)
        self.assertEqual(self.sent, [])
        self.accept = True
        self.wait(1)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.b.pending, {})

    def test_a_share_arriving_while_one_waits_follows_it_in_order(self):
        self.accept = False
        self.b.add("ana", "file", "inbox/a.png", i=1, n=1)
        self.b.add("ana", "file", "inbox/b.png", i=1, n=1)     # the next share, while the first waits
        self.accept = True
        self.wait(1)
        self.assertEqual([m for _, m in self.sent],
                         ["📎 shared from phone: inbox/a.png  — have a look", "📎 shared from phone: inbox/b.png  — have a look"])
        self.assertEqual(self.b.pending, {})

    def test_stuck_is_reported_once(self):
        self.accept = False
        self.b.add("ana", "file", "inbox/a.png", i=1, n=1)
        for _ in range(130):
            self.wait(1)
        self.assertEqual(self.stuck, [("ana", 1)])


class SharedText(unittest.TestCase):
    """WhatsApp hands its texts to the Share Sheet as small text files named after their first words."""

    def test_whatsapp_texts_are_messages(self):
        t = b"The other MSCONS for single measurement or Energiemengen already exist, I just need to verify them and then they need to be automated"
        self.assertTrue(shared_text("The other MSCONS for single measurement or Energiemengen already exist, I just need to verify them a.txt", t))
        t = b"Maybe we can practice with Hamburg, to reply to all of them automatically."
        self.assertTrue(shared_text("Maybe we can practice with Hamburg, to reply to all of them automatically.", t))

    def test_real_files_stay_files(self):
        self.assertFalse(shared_text("notes.txt", b"shopping list\nmilk\neggs"))        # its own name
        self.assertFalse(shared_text("report.csv", b"a,b\n1,2"))                        # not a text share
        self.assertFalse(shared_text("IMG_1.jpg", b"\xff\xd8\xff\xe0binary"))
        self.assertFalse(shared_text("big.txt", b"x" * 20000))
        self.assertFalse(shared_text("Image", b"\x89PNG\r\n\x1a\n\x00"))


if __name__ == "__main__":
    unittest.main()
