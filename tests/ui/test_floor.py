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
        # a terminal is a blank page here: no test ever attaches to a real one
        self.page.route("**/terminal/**", lambda r: r.fulfill(status=200, content_type="text/html", body="<html></html>"))

    def open_panel(self, session):
        self.page.hover(f"[data-session={session}]")
        self.page.click(f"[data-session={session}] .peek")

    def _catch(self, route):
        self.writes.append((route.request.url.rsplit("/", 1)[-1], route.request.post_data))
        route.fulfill(status=200, content_type="application/json", body='{"ok":true}')

    def test_everyone_is_one_list_by_activity_and_a_room_shows_only_its_desks(self):
        self.open_floor()
        heads = "[...document.querySelectorAll('.rhead')].map(h=>h.firstChild.textContent)"
        names = "[...document.querySelectorAll('.desk .wn')].map(n=>n.textContent)"
        self.assertEqual(self.js(heads), ["Pinned", "Today"])
        # whatever the room: pinned first, then the most recently active, the one not running last
        self.assertEqual(self.js(names), ["Bo", "Zed", "Cy", "Amy", "Di"])
        self.assertEqual(self.js("getComputedStyle(document.querySelector('[data-session=cy-delta] .rtag')).display"), "block")    # shown (a flex item computes to block)
        self.assertEqual(self.js("document.querySelector('[data-session=cy-delta] .rtag').textContent"), "Personal")
        self.assertEqual(self.js("[...document.querySelectorAll('#roomsw button')].map(b=>b.textContent)"),
                         ["Everyone5", "Ventures1", "Clients2", "Personal2"])
        self.page.click("#roomsw button[data-room=clients]")
        self.assertEqual(self.js(names), ["Zed", "Amy"])
        self.assertEqual(self.js("getComputedStyle(document.querySelector('[data-session=zed-alpha] .rtag')).display"), "none")
        self.page.click("#roomsw button[data-room=all]")
        self.assertEqual(len(self.js(names)), 5)
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

    def test_a_click_opens_the_terminal_and_read_opens_the_panel_and_the_tab_says_whose(self):
        self.open_floor()
        base = self.js("document.title")
        self.page.click("[data-session=zed-alpha] .wn")
        self.assertTrue(self.js("MODAL_OPEN"))
        self.assertEqual(self.js("MODAL_SESSION"), "zed-alpha")
        self.assertTrue(self.js("document.getElementById('desk').hidden"))
        self.assertEqual(self.js("document.title"), "Alpha | Zed")
        self.js("closeTerm()")
        self.assertEqual(self.js("document.title"), base)
        self.open_panel("amy-beta")
        self.assertFalse(self.js("MODAL_OPEN"))
        self.assertFalse(self.js("document.getElementById('desk').hidden"))
        self.assertEqual(self.js("document.title"), "Beta | Amy")
        self.page.keyboard.press("Escape")
        self.assertEqual(self.js("document.title"), base)
        # a desk that isn't running opens its panel: there's no terminal to show
        self.page.click("[data-session=di-eps] .wn")
        self.assertFalse(self.js("MODAL_OPEN"))
        self.assertFalse(self.js("document.getElementById('desk').hidden"))

    def test_beside_the_terminal_a_chat_list_says_who_how_long_ago_and_whats_going_on_and_keeps_up(self):
        team = [dict(m) for m in TEAM]
        self.page.unroute("**/api/floor")
        self.page.route("**/api/floor", lambda r: r.fulfill(status=200, content_type="application/json",
                                                          body=json.dumps({"team": team, "version": "test"})))
        self.open_floor()
        self.page.click("[data-session=zed-alpha] .wn")
        self.page.wait_for_selector("#rail .rp")
        rows = lambda: self.js("[...document.querySelectorAll('#rail .rp')].map(b=>[b.dataset.s, b.querySelector('.ra').textContent, b.querySelector('.r2').textContent])")
        self.assertEqual(rows(), [["cy-delta", "1m", "needs you: Permission to use Bash"],
                                  ["amy-beta", "2m", "needs you: Ship 2.8.1 now?"],
                                  ["bo-gamma", "now", "working"],
                                  ["zed-alpha", "1m", "done · Passt das so?"]])          # a desk that isn't running isn't listed
        self.assertEqual(self.js("document.querySelector('#rail .rp.on').dataset.s"), "zed-alpha")
        self.assertEqual(self.js("[...document.querySelectorAll('#rail .rg')].map(g=>g.textContent)"), ["Needs you · 2", "Recent"])
        # Bo finishes while the terminal is open: the list follows without reopening anything
        team[2].update(state="review", full="## Built\n\nall 40 pages", unread=True)
        self.page.wait_for_function("[...document.querySelectorAll('#rail .r2')].some(e=>e.textContent==='done · Built')", timeout=10000)
        self.assertEqual(self.js("document.querySelector('#rail .rp.on .rn').textContent"), "Alpha")   # the project, as the tab says it
        self.assertEqual(self.js("document.querySelector('#rail .rp.on .pn').textContent"), "Zed")     # the agent under its face
        # drag its edge to make it wider; the width is kept, a double-click puts it back
        box = self.page.locator("#railgrip").bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + 200
        self.page.mouse.move(x, y); self.page.mouse.down(); self.page.mouse.move(x + 90, y, steps=5); self.page.mouse.up()
        self.assertEqual(self.js("Math.round(document.getElementById('rail').getBoundingClientRect().width)"), 340)
        self.assertEqual(self.js("pref('railW')"), 340)
        self.page.mouse.move(x + 90, y); self.page.mouse.down(); self.page.mouse.move(x + 900, y, steps=5); self.page.mouse.up()
        self.assertEqual(self.js("pref('railW')"), 480)                                    # never wider than that
        self.page.dblclick("#railgrip")
        self.assertEqual(self.js("pref('railW')"), 250)
        # ‹ hide folds it to faces, and that is remembered
        self.page.click("#rail .rfold")
        self.assertTrue(self.js("document.getElementById('rail').classList.contains('fold')"))
        self.assertEqual(self.js("getComputedStyle(document.querySelector('#rail .rt')).display"), "none")
        self.assertTrue(self.js("pref('railFold')"))
        self.page.click("#rail .rfold")
        self.assertFalse(self.js("document.getElementById('rail').classList.contains('fold')"))

    def test_a_desk_opens_its_panel_and_a_choice_presses_its_key(self):
        self.open_floor()
        self.open_panel("amy-beta")
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
        self.open_panel("zed-alpha")
        self.assertIn("<table>", self.js("document.querySelector('#dp-body .dp-reply').innerHTML"))
        self.page.fill("#dp-in", "line one")
        self.page.keyboard.press("Shift+Enter")
        self.assertEqual(self.writes, [])
        self.page.keyboard.press("Enter")
        self.page.wait_for_timeout(200)
        self.assertEqual(json.loads(self.writes[0][1])["session"], "zed-alpha")

    def test_a_permission_prompt_allows_with_1_and_denies_with_esc(self):
        self.open_floor()
        self.open_panel("cy-delta")
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
        self.open_panel("zed-alpha")
        self.page.wait_for_function("document.querySelectorAll('#dp-body .dp-you').length===2")
        self.assertEqual(self.js("[...document.querySelectorAll('#dp-body .dp-you')].map(b=>b.textContent)"), ["first ask", "second ask"])
        replies = self.js("[...document.querySelectorAll('#dp-body .dp-reply')].map(b=>b.textContent.trim())")
        self.assertEqual(replies[:2], ["one", "looking into it"])
        self.assertTrue(replies[2].endswith(" end") and len(replies[2]) > 5000)     # nothing cut

    def test_a_picture_you_pasted_shows_under_your_message_and_opens_big(self):
        turns = [{"you": "see inbox/Screen shot 1.png and inbox/notes.pdf", "ts": NOW - 60, "said": ["ok"]}]
        asked = []
        png = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                            "1f15c4890000000d49444154789c6360000002000100e5279dee0000000049454e44ae426082")

        def pic(r):
            asked.append(r.request.url)
            r.fulfill(status=200, content_type="image/png", body=png)
        self.page.route("**/api/turns?session=zed-alpha", lambda r: r.fulfill(
            status=200, content_type="application/json", body=json.dumps({"turns": turns})))
        self.page.route("**/api/inbox?*", pic)
        self.open_floor()
        self.open_panel("zed-alpha")
        self.page.wait_for_function("(i=>i && i.complete && i.naturalWidth>0)(document.querySelector('#dp-body .dp-pic img'))")
        self.assertEqual(self.js("document.querySelectorAll('#dp-body .dp-pic').length"), 1)
        self.assertIn("session=zed-alpha", asked[0])
        self.assertIn("path=inbox%2FScreen%20shot%201.png", asked[0])     # the pdf gets no preview
        self.page.click("#dp-body .dp-pic")
        self.page.wait_for_timeout(200)
        self.assertTrue(self.js("document.getElementById('viewdlg').open"))
        self.assertEqual(self.js("document.getElementById('vw-name').textContent"), "Screen shot 1.png")

    def test_a_paste_shows_how_far_it_has_gone_and_still_lands_with_its_agent_after_you_switch(self):
        held, typed = [], []
        self.page.route("**/api/upload?*", lambda r: held.append(r))           # still going up
        self.page.route("**/api/paste", lambda r: (typed.append(json.loads(r.request.post_data)),
                                                    r.fulfill(status=200, content_type="application/json", body='{"ok":true}')))
        self.open_floor()
        self.page.evaluate("openTermByName('zed-alpha')")
        self.page.evaluate("()=>{ insertUploads([new File([new Uint8Array(4096)], 'shot.png', {type:'image/png'})]); }")   # not awaited: it waits on the upload
        self.page.wait_for_function("document.getElementById('upbar').classList.contains('show')")
        self.assertEqual(self.js("document.querySelector('#upbar .ut').textContent"), "Uploading shot.png to Zed")
        self.page.wait_for_function("document.querySelector('#upbar .un').textContent.includes('of')")
        self.page.wait_for_timeout(100)
        self.assertEqual(len(held), 1)
        self.assertIn("session=zed-alpha", held[0].request.url)
        self.page.evaluate("openTermByName('amy-beta')")                        # you move on while it uploads
        held[0].fulfill(status=200, content_type="application/json", body='{"ok":true,"path":"inbox/shot.png"}')
        self.page.wait_for_function("!document.getElementById('upbar').classList.contains('show')")
        self.page.wait_for_timeout(200)
        self.assertEqual(typed, [{"session": "zed-alpha", "text": "inbox/shot.png "}])
        self.assertIn("Zed", self.js("document.getElementById('toast').textContent"))

    def test_the_plus_in_the_panel_attaches_a_file_and_puts_its_path_in_the_reply_box(self):
        asked = []
        self.page.route("**/api/upload?*", lambda r: (asked.append(r.request.url), r.fulfill(
            status=200, content_type="application/json", body='{"ok":true,"path":"inbox/shot.png"}')))
        self.page.route("**/api/paste", self._catch)
        self.open_floor()
        self.open_panel("zed-alpha")
        self.page.fill("#dp-in", "look at")
        with self.page.expect_file_chooser() as fc:
            self.page.click("#dp-compose .attach")
        fc.value.set_files(files=[{"name": "shot.png", "mimeType": "image/png", "buffer": b"\x89PNG" + bytes(100)}])
        self.page.wait_for_function("document.getElementById('dp-in').value.includes('inbox/')")
        self.assertEqual(self.js("document.getElementById('dp-in').value"), "look at inbox/shot.png ")
        self.assertIn("session=zed-alpha", asked[0])
        self.assertEqual([w for w in self.writes if w[0] == "paste"], [])            # nothing typed into the terminal
        self.assertEqual(self.writes, [])                                            # and nothing sent

    def test_while_it_works_the_panel_shows_its_spinner_word_the_time_and_its_latest_step(self):
        bo = next(m for m in TEAM if m["session"] == "bo-gamma")
        team = [dict(m, you_at=NOW - 75, verb="Metamorphosing…", doing="Run the full test suite") if m is bo else m for m in TEAM]
        self.page.unroute("**/api/floor")
        self.page.route("**/api/floor", lambda r: r.fulfill(status=200, content_type="application/json",
                                                          body=json.dumps({"team": team, "version": "test"})))
        self.open_floor()
        self.open_panel("bo-gamma")
        self.page.wait_for_selector("#dp-body .working-note")
        self.assertEqual(self.js("document.querySelector('#dp-body .working-note .vb').textContent"), "Metamorphosing…")
        self.assertEqual(self.js("document.querySelector('#dp-body .working-note .el').textContent"), "1m 15s")
        self.assertEqual(self.js("document.querySelector('#dp-body .doing').textContent"), "Run the full test suite")

    def test_a_terminal_that_stays_blank_is_reloaded_once_and_reported(self):
        loads, diag = [], []
        self.page.unroute("**/terminal/**")
        self.page.route("**/terminal/**", lambda r: (loads.append(r.request.url), r.fulfill(
            status=200, content_type="text/html", body="<html><body></body></html>")))      # never draws anything
        self.page.route("**/api/termdiag", lambda r: (diag.append(json.loads(r.request.post_data)), r.fulfill(
            status=200, content_type="application/json", body='{"ok":true}')))
        self.open_floor()
        self.page.evaluate("openTermByName('zed-alpha')")
        self.page.wait_for_timeout(4200)
        self.assertEqual(len(loads), 2)                                   # reloaded once
        self.assertEqual([(d["why"], d["session"]) for d in diag], [("blank after 3.5s", "zed-alpha")])
        self.page.wait_for_timeout(4000)
        self.assertEqual(len(loads), 2)                                   # and not again and again

    def test_a_desk_whose_icon_is_a_logo_shows_the_logo_on_its_card_and_in_its_panel(self):
        team = [dict(m, emoji="img:alpha.svg") if m["session"] == "zed-alpha" else m for m in TEAM]
        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10" fill="red"/></svg>'
        self.page.unroute("**/api/floor")
        self.page.route("**/api/floor", lambda r: r.fulfill(status=200, content_type="application/json",
                                                          body=json.dumps({"team": team, "version": "test"})))
        self.page.route("**/logos/alpha.svg", lambda r: r.fulfill(status=200, content_type="image/svg+xml", body=svg))
        self.open_floor()
        self.assertEqual(self.js("document.querySelector('[data-session=zed-alpha] img.facelogo').getAttribute('src')"), "/logos/alpha.svg")
        self.assertIsNone(self.js("document.querySelector('[data-session=amy-beta] img.facelogo')"))   # an emoji stays an emoji
        self.open_panel("zed-alpha")
        self.assertEqual(self.js("document.querySelector('#dp-av img.facelogo').getAttribute('src')"), "/logos/alpha.svg")

    def test_what_you_send_shows_at_once_with_replying_under_it(self):
        held = []
        self.page.unroute("**/api/say")
        self.page.route("**/api/say", lambda r: held.append(r))  # the agent hasn't taken it yet
        self.open_floor()
        self.open_panel("zed-alpha")
        self.page.fill("#dp-in", "ship it")
        self.page.keyboard.press("Enter")
        self.page.wait_for_timeout(200)
        order = "[...document.querySelectorAll('#dp-body .dp-you, #dp-body .working-note, #dp-body .doing')].map(e=>e.className)"
        self.assertEqual(self.js(order)[-3:], ["dp-you", "working-note", "doing"])
        self.assertEqual(self.js("document.querySelector('#dp-body .doing').textContent"), "Sent, waiting for it to start")
        self.assertEqual(self.js("document.getElementById('dp-in').value"), "")
        held[0].fulfill(status=200, content_type="application/json", body='{"error":"not running"}')
        self.page.wait_for_timeout(300)                           # a failed send takes it back
        self.assertEqual(self.js("document.querySelectorAll('#dp-body .working-note').length"), 0)
        self.assertEqual(self.js("document.getElementById('dp-in').value"), "ship it")

    def test_an_icon_you_pick_for_one_desk(self):
        self.open_floor()
        self.open_panel("zed-alpha")
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
        self.open_panel("zed-alpha")
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
        self.open_panel("zed-alpha")
        self.page.click("#dp-more")
        items = self.js("[...document.querySelectorAll('#dp-menu button')].map(b=>b.textContent)")
        self.assertIn("Pin to the front", items)
        self.assertIn("Move to Ventures", items)
        self.assertNotIn("Move to Clients", items)
        self.page.click("#dp-menu button[data-act='mv:ventures']")
        self.page.wait_for_timeout(200)
        self.assertEqual(self.writes[0][0], "group")
        self.assertTrue(self.js("document.getElementById('dp-menu').hidden"))


    def test_dragging_a_desk_onto_a_room_moves_it(self):
        self.open_floor()
        self.page.drag_and_drop("[data-session=zed-alpha]", "#roomsw button[data-room=personal]")
        self.page.wait_for_timeout(200)
        self.assertEqual(self.writes[0][0], "group")
        self.assertEqual(json.loads(self.writes[0][1]), {"session": "zed-alpha", "group": "personal"})
        # dropping on its own room does nothing
        self.page.drag_and_drop("[data-session=bo-gamma]", "#roomsw button[data-room=ventures]")
        self.page.wait_for_timeout(200)
        self.assertEqual(len(self.writes), 1)


@skip_unless_browser
class PhoneFloor(BrowserCase):
    device = IPHONE

    def test_closing_the_terminal_comes_back_to_where_you_were_on_the_floor(self):
        self.page.route("**/api/floor", lambda r: r.fulfill(status=200, content_type="application/json",
                                                          body=json.dumps({"team": TEAM, "version": "test"})))
        self.page.route("**/terminal/**", lambda r: r.fulfill(status=200, content_type="text/html", body="<html></html>"))
        for api in ("say", "key", "paste"):
            self.page.route(f"**/api/{api}", lambda r: r.fulfill(status=200, content_type="application/json", body='{"ok":true}'))
        self.open_floor()
        self.page.evaluate("document.body.style.minHeight='3000px'; window.scrollTo(0, 400)")
        self.page.evaluate("openTermByName('zed-alpha')")
        self.page.wait_for_timeout(200)
        self.page.evaluate("window.scrollTo(0, 1500)")          # what the keyboard does to the page under the terminal
        self.page.evaluate("closeTerm()")
        self.page.wait_for_timeout(800)
        self.assertEqual(self.js("Math.round(scrollY)"), 400)

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
