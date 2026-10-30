"""The floor as rooms of desks, and a person's panel. /api/floor is stubbed with made-up agents and
every write (/api/say, /api/pin, …) is caught here, so nothing reaches a real agent."""
import json

from ._base import BrowserCase, DESKTOP, IPHONE, skip_unless_browser

NOW = 1_900_000_000
TEAM = [
    {"session": "zed-alpha", "person": "Zed", "project": "alpha", "group": "clients", "state": "review",
     "ts": NOW - 60, "full": "Passt das so?\n\nDone.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nAll green now.", "emoji": "🧾", "unread": True},
    {"session": "amy-beta", "person": "Amy", "project": "beta", "group": "clients", "state": "waiting",
     "ts": NOW - 120, "full": "Ship it?", "ask": "Ship 2.8.1 now?", "asks": "question", "options": ["yes", "no"], "emoji": "🫀"},
    {"session": "bo-gamma", "person": "Bo", "project": "gamma", "group": "ventures", "state": "working",
     "ts": NOW - 30, "full": "Building…", "pinned": True},
    {"session": "cy-delta", "person": "Cy", "project": "delta", "group": "personal", "state": "waiting",
     "ts": NOW - 90, "full": "ok", "ask": "Permission to use Bash", "asks": "permission"},
    {"session": "di-eps", "person": "Di", "project": "eps", "group": "personal", "state": "review",
     "ts": NOW - 95, "full": "bye", "running": False},
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
        for api in ("say", "pin", "mute", "group", "task", "kill", "rename", "key", "icon", "brief"):
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
        self.assertEqual(self.js("document.querySelector('[data-session=cy-delta] .note').textContent"), "Allow")
        self.assertTrue(self.js("document.querySelector('[data-session=di-eps]').classList.contains('ghost')"))
        self.assertIsNone(self.js("document.querySelector('[data-session=zed-alpha] .note')"))
        self.assertTrue(self.js("!!document.querySelector('[data-session=bo-gamma] .pinhead')"))
        self.assertTrue(self.js("!!document.querySelector('[data-session=bo-gamma] .wdot')"))
        # the line is only what Claude Code says it waits for; a "?" in a finished reply is not a question
        self.assertEqual(self.js("document.querySelector('[data-session=amy-beta] .line').textContent"), "Ship 2.8.1 now?")
        self.assertEqual(self.js("document.querySelector('[data-session=zed-alpha] .line').textContent"), "")
        self.assertEqual(self.js("document.querySelector('[data-session=di-eps] .line').textContent"), "Not running")
        self.assertEqual(self.js("[...document.querySelectorAll('#recent .mchip')].map(b=>b.textContent.trim())"), ["👤Cy allow", "🫀Amy answer"])

    def test_a_desk_opens_its_panel_and_a_choice_presses_its_key(self):
        self.open_floor()
        self.page.click("[data-session=amy-beta]")
        self.assertFalse(self.js("document.getElementById('desk').hidden"))
        self.assertEqual(self.js("document.getElementById('dp-name').textContent"), "Amy")
        self.assertTrue(self.js("document.querySelector('[data-session=amy-beta]').classList.contains('sel')"))
        self.assertEqual(self.js("document.querySelector('#dp-body .dp-q .qh').textContent"), "Ship 2.8.1 now?")
        self.assertEqual(self.js("[...document.querySelectorAll('#dp-body .opt span')].map(s=>s.textContent)"), ["yes", "no"])
        # the choices are a menu in the terminal: a button presses that choice's digit there
        self.page.click("#dp-body .opt >> nth=1")
        self.page.wait_for_timeout(200)
        self.assertEqual(self.writes[0][0], "key")
        self.assertEqual(json.loads(self.writes[0][1]), {"session": "amy-beta", "key": "2"})
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

    def test_a_permission_prompt_allows_with_1_and_denies_with_esc(self):
        self.open_floor()
        self.page.click("[data-session=cy-delta]")
        self.assertEqual(self.js("[...document.querySelectorAll('#dp-body .opt span')].map(s=>s.textContent)"), ["Allow", "Deny"])
        self.page.click("#dp-body .opt >> nth=1")
        self.page.wait_for_timeout(200)
        self.assertEqual(json.loads(self.writes[0][1]), {"session": "cy-delta", "key": "esc"})

    def test_the_panel_shows_the_last_exchanges_whole(self):
        turns = [{"you": "first ask", "ts": NOW - 3600, "said": ["one"]},
                 {"you": "second ask", "ts": NOW - 60, "said": ["looking into it", "x" * 5000 + " end"]}]
        self.page.route("**/api/turns?session=zed-alpha", lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps({"turns": turns})))
        self.open_floor()
        self.page.click("[data-session=zed-alpha]")
        self.page.wait_for_function("document.querySelectorAll('#dp-body .dp-you').length===2")
        self.assertEqual(self.js("[...document.querySelectorAll('#dp-body .dp-you')].map(b=>b.textContent)"), ["first ask", "second ask"])
        replies = self.js("[...document.querySelectorAll('#dp-body .dp-reply')].map(b=>b.textContent.trim())")
        self.assertEqual(replies[:2], ["one", "looking into it"])
        self.assertTrue(replies[2].endswith(" end") and len(replies[2]) > 5000)     # nothing cut

    def test_an_icon_you_pick_for_one_desk(self):
        self.open_floor()
        self.page.click("[data-session=zed-alpha]")
        self.page.click("#dp-more")
        self.page.click("#dp-menu button[data-act='icon']")
        self.assertEqual(self.page.input_value("#icondlg input"), "🧾")      # starts from the icon it has now
        self.page.fill("#icondlg input", "🦊")
        self.page.click("#icondlg button[value=save]")
        self.page.wait_for_timeout(200)
        self.assertEqual((self.writes[0][0], json.loads(self.writes[0][1])), ("icon", {"session": "zed-alpha", "emoji": "🦊"}))
        # Reset sends a blank: back to the guess from the project name
        self.page.click("#dp-more")
        self.page.click("#dp-menu button[data-act='icon']")
        self.page.click("#icondlg .iconreset")
        self.page.wait_for_timeout(200)
        self.assertEqual(json.loads(self.writes[1][1]), {"session": "zed-alpha", "emoji": ""})

    def test_a_new_desk_opens_its_panel(self):
        team = list(TEAM)
        self.page.unroute("**/api/floor")
        self.page.route("**/api/floor", lambda r: r.fulfill(status=200, content_type="application/json",
                                                          body=json.dumps({"team": team, "version": "test"})))
        for api in ("dirs", "history"):
            self.page.route(f"**/api/{api}", lambda r: r.fulfill(status=200, content_type="application/json", body="{}"))

        def new(route):
            team.append({"session": "ed-zeta", "person": "Ed", "project": "zeta", "group": "clients",
                         "state": "review", "ts": NOW, "full": ""})
            route.fulfill(status=200, content_type="application/json", body='{"ok":true,"created":"ed-zeta"}')
        self.page.route("**/api/new", new)
        self.open_floor()
        self.page.click("#newbtn")
        self.page.fill("#newform input[name=who]", "ed")
        self.page.fill("#newform input[name=project]", "zeta")
        self.page.click("#newform button[value=create]")
        self.page.wait_for_function("DESK==='ed-zeta'")
        self.assertFalse(self.js("document.getElementById('desk').hidden"))

    def test_a_reply_you_havent_seen_has_a_dot_until_you_open_it(self):
        self.open_floor()
        self.assertTrue(self.js("!!document.querySelector('[data-session=zed-alpha] .att')"))
        self.assertFalse(self.js("!!document.querySelector('[data-session=amy-beta] .att')"))
        self.page.click("[data-session=zed-alpha]")
        self.assertFalse(self.js("!!document.querySelector('[data-session=zed-alpha] .att')"))

    def test_whats_new_lists_who_needs_you_and_opens_them(self):
        brief = {"ok": True, "text": "x", "audio": "", "working": ["Bo"],
                 "items": [{"session": "amy-beta", "name": "Amy", "kind": "question", "line": "asks whether to ship"},
                           {"session": "zed-alpha", "name": "Zed", "kind": "finished", "line": "done, test steps 1-3"}]}
        self.page.unroute("**/api/brief")
        self.page.route("**/api/brief", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(brief)))
        self.open_floor()
        self.page.click("#sec-brief")
        self.page.wait_for_selector("#sec-reply .brow")
        self.assertEqual(self.js("[...document.querySelectorAll('#sec-reply .brow b')].map(b=>b.textContent)"), ["Amy", "Zed"])
        self.assertTrue(self.js("document.querySelector('#sec-reply .brow').classList.contains('waits')"))
        self.assertIn("Bo", self.js("document.querySelector('#sec-reply .bfoot').textContent"))
        self.page.click("#sec-reply .brow >> nth=1")
        self.assertEqual(self.js("DESK"), "zed-alpha")

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

    def test_a_pull_inside_the_panel_scrolls_it_and_a_reload_comes_back_to_it(self):
        long = dict(TEAM[0], full="\n\n".join(f"line {i}" for i in range(80)))
        self.page.route("**/api/floor", lambda r: r.fulfill(status=200, content_type="application/json",
                                                          body=json.dumps({"team": [long] + TEAM[1:], "version": "test"})))
        self.open_floor()
        self.js("openDesk('zed-alpha')")
        self.js("const b=document.getElementById('dp-body'); b.scrollTop=b.scrollHeight")
        # a finger dragged down inside the panel: the pull-to-refresh must not take it
        self.js("""(()=>{const b=document.getElementById('dp-body'), t=(y)=>new Touch({identifier:1,target:b,clientX:200,clientY:y});
            b.dispatchEvent(new TouchEvent('touchstart',{touches:[t(200)],bubbles:true}));
            const mv=new TouchEvent('touchmove',{touches:[t(400)],bubbles:true,cancelable:true});
            b.dispatchEvent(mv); window._prevented=mv.defaultPrevented;
            b.dispatchEvent(new TouchEvent('touchend',{touches:[],bubbles:true}));})()""")
        self.assertFalse(self.js("window._prevented"))
        self.assertFalse(self.js("document.getElementById('ptr').classList.contains('show')"))
        self.page.reload()
        self.page.wait_for_selector(".desk")
        self.page.wait_for_timeout(300)
        self.assertEqual(self.js("DESK"), "zed-alpha")
        self.assertFalse(self.js("document.getElementById('desk').hidden"))
