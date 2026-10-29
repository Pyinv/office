"""The floor as rooms of desks, and a person's panel. /api/floor is stubbed with made-up agents and
every write (/api/say, /api/pin, …) is caught here, so nothing reaches a real agent."""
import json

from ._base import BrowserCase, DESKTOP, IPHONE, skip_unless_browser

NOW = 1_900_000_000
TEAM = [
    {"session": "zed-alpha", "person": "Zed", "project": "alpha", "group": "clients", "state": "review",
     "ts": NOW - 60, "full": "Done.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nAll green now.", "emoji": "🧾"},
    {"session": "amy-beta", "person": "Amy", "project": "beta", "group": "clients", "state": "waiting",
     "ts": NOW - 120, "full": "Ship it?", "ask": "Ship 2.8.1 now?", "options": ["yes", "no"], "emoji": "🫀"},
    {"session": "bo-gamma", "person": "Bo", "project": "gamma", "group": "ventures", "state": "working",
     "ts": NOW - 30, "full": "Building…", "pinned": True},
    {"session": "cy-delta", "person": "Cy", "project": "delta", "group": "personal", "state": "review",
     "ts": NOW - 90, "full": "ok", "typed": "run the tests again"},
]


@skip_unless_browser
class Floor(BrowserCase):
    device = DESKTOP

    def setUp(self):
        super().setUp()
        self.writes = []
        self.page.add_init_script(f"Date.now=()=>{NOW * 1000}")
        self.page.route("**/api/floor", lambda r: r.fulfill(status=200, content_type="application/json",
                                                          body=json.dumps({"team": TEAM, "version": "test"})))
        for api in ("say", "pin", "mute", "group", "task", "kill", "rename"):
            self.page.route(f"**/api/{api}", self._catch)

    def _catch(self, route):
        self.writes.append((route.request.url.rsplit("/", 1)[-1], route.request.post_data))
        route.fulfill(status=200, content_type="application/json", body='{"ok":true}')

    def test_rooms_hold_desks_by_activity_and_the_needs_you_note(self):
        self.open_floor()
        self.assertEqual(self.js("[...document.querySelectorAll('.rhead')].map(h=>h.firstChild.textContent)"),
                         ["Ventures", "Clients", "Personal"])
        # inside a room the most recently active comes first: Zed (1 min ago) before Amy (2 min ago)
        self.assertEqual(self.js("[...[...document.querySelectorAll('.room')][1].querySelectorAll('.desk .wn')].map(n=>n.textContent)"),
                         ["Zed", "Amy"])
        self.assertEqual(self.js("document.querySelector('[data-session=amy-beta] .note').textContent"), "Answer")
        self.assertEqual(self.js("document.querySelector('[data-session=cy-delta] .note').textContent"), "Draft")
        self.assertIsNone(self.js("document.querySelector('[data-session=zed-alpha] .note')"))
        self.assertTrue(self.js("!!document.querySelector('[data-session=bo-gamma] .pinhead')"))
        self.assertTrue(self.js("!!document.querySelector('[data-session=bo-gamma] .wdot')"))
        # the line is the question, and for a finished reply its last readable line (no table rules)
        self.assertEqual(self.js("document.querySelector('[data-session=amy-beta] .line').textContent"), "Ship 2.8.1 now?")
        self.assertEqual(self.js("document.querySelector('[data-session=zed-alpha] .line').textContent"), "All green now.")
        self.assertEqual(self.js("[...document.querySelectorAll('#recent .mchip')].map(b=>b.textContent.trim())"), ["🫀Amy answer"])

    def test_a_desk_opens_its_panel_and_answers_go_through_say(self):
        self.open_floor()
        self.page.click("[data-session=amy-beta]")
        self.assertFalse(self.js("document.getElementById('desk').hidden"))
        self.assertEqual(self.js("document.getElementById('dp-name').textContent"), "Amy")
        self.assertTrue(self.js("document.querySelector('[data-session=amy-beta]').classList.contains('sel')"))
        self.assertEqual(self.js("[...document.querySelectorAll('#dp-body .opt span')].map(s=>s.textContent)"), ["yes", "no"])
        self.page.click("#dp-body .opt >> nth=0")
        self.page.wait_for_timeout(200)
        self.assertEqual(self.writes[0][0], "say")
        self.assertEqual(json.loads(self.writes[0][1]), {"session": "amy-beta", "text": "yes"})
        self.page.keyboard.press("Escape")
        self.assertTrue(self.js("document.getElementById('desk').hidden"))

    def test_the_reply_box_sends_on_enter_and_keeps_shift_enter(self):
        self.open_floor()
        self.page.click("[data-session=zed-alpha]")
        self.assertIn("<table>", self.js("document.querySelector('#dp-body .dp-reply').innerHTML"))
        self.page.fill("#dp-in", "line one")
        self.page.keyboard.press("Shift+Enter")
        self.assertEqual(self.writes, [])
        self.page.keyboard.press("Enter")
        self.page.wait_for_timeout(200)
        self.assertEqual(json.loads(self.writes[0][1])["session"], "zed-alpha")

    def test_the_menu_pins_and_moves(self):
        self.open_floor()
        self.page.click("[data-session=zed-alpha]")
        self.page.click("#dp-more")
        items = self.js("[...document.querySelectorAll('#dp-menu button')].map(b=>b.textContent)")
        self.assertIn("Pin to the front", items)
        self.assertIn("Move to Ventures", items)
        self.assertNotIn("Move to Clients", items)
        self.page.click("#dp-menu button[data-act='mv:ventures']")
        self.page.wait_for_timeout(200)
        self.assertEqual(self.writes[0][0], "group")
        self.assertTrue(self.js("document.getElementById('dp-menu').hidden"))


    def test_dragging_a_desk_to_another_room_moves_it(self):
        self.open_floor()
        self.page.drag_and_drop("[data-session=zed-alpha]", ".room[data-room=personal] .rhead")
        self.page.wait_for_timeout(200)
        self.assertEqual(self.writes[0][0], "group")
        self.assertEqual(json.loads(self.writes[0][1]), {"session": "zed-alpha", "group": "personal"})
        # dropping on its own room does nothing
        self.page.drag_and_drop("[data-session=bo-gamma]", ".room[data-room=ventures] .rhead")
        self.page.wait_for_timeout(200)
        self.assertEqual(len(self.writes), 1)


@skip_unless_browser
class PhoneFloor(BrowserCase):
    device = IPHONE

    def test_three_seats_a_row_and_the_panel_fills_the_screen(self):
        self.page.route("**/api/floor", lambda r: r.fulfill(status=200, content_type="application/json",
                                                          body=json.dumps({"team": TEAM, "version": "test"})))
        self.open_floor()
        self.assertEqual(self.js("getComputedStyle(document.querySelector('.desks')).gridTemplateColumns.split(' ').length"), 3)
        self.assertEqual(self.js("getComputedStyle(document.querySelector('.desk .line')).display"), "none")
        self.js("openDesk('amy-beta')")
        self.assertEqual(self.js("Math.round(document.getElementById('desk').getBoundingClientRect().width)"), 390)
