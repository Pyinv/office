"""The header's account pill and its switch dialog. /api/accounts is stubbed with made-up accounts and
/api/account (the switch) is caught here, so the real login is never touched."""
import json

from ._base import BrowserCase, IPHONE, skip_unless_browser

ACCOUNTS = {"current": "one@example.test", "accounts": [
    {"email": "one@example.test", "label": "work", "current": True, "state": "ok", "live": True, "fetched_at": 1,
     "limits": [{"label": "session", "percent": 4, "resets_at": None}, {"label": "week", "percent": 83, "resets_at": None}]},
    {"email": "two@example.test", "label": "home", "current": False, "state": "ok", "live": False, "fetched_at": 1,
     "limits": [{"label": "week", "percent": 10, "resets_at": None}]},
    {"email": "three@example.test", "label": "", "current": False, "state": "login",
     "note": "never logged in here: the first switch to it needs one browser login"},
]}


@skip_unless_browser
class AccountPill(BrowserCase):
    def setUp(self):
        super().setUp()
        self.switched = []
        self.page.route("**/api/accounts*", lambda r: r.fulfill(status=200, content_type="application/json",
                                                                body=json.dumps(ACCOUNTS)))
        self.page.unroute("**/api/account")
        self.page.route("**/api/account", self._switch)

    def _switch(self, route):
        self.switched.append(json.loads(route.request.post_data))
        route.fulfill(status=200, content_type="application/json", body='{"ok":true}')

    def test_the_pill_shows_who_and_the_fullest_limit_in_place_of_the_usage_link(self):
        self.open_floor()
        self.assertEqual(self.js("document.getElementById('acctbtn').textContent"), "👤 work83%")
        self.assertTrue(self.js("document.getElementById('acctbtn').classList.contains('hot')"))
        self.assertEqual(self.js("getComputedStyle(document.getElementById('usagelink')).display"), "none")

    def test_switching_takes_two_taps_and_an_account_needing_the_browser_has_no_button(self):
        self.open_floor()
        self.page.click("#acctbtn")
        self.page.wait_for_timeout(300)
        self.assertTrue(self.js("document.getElementById('acctdlg').open"))
        names = self.js("[...document.querySelectorAll('#ac-list .nm b')].map(b=>b.textContent)")
        self.assertEqual(names, ["work", "home", "three"])
        self.assertEqual(self.js("document.querySelector('#ac-list .tag').textContent"), "Logged in")
        self.assertEqual(self.js("document.querySelectorAll('#ac-list button[data-email]').length"), 1)
        self.assertIn("run claude-switch in a terminal", self.js("document.querySelectorAll('#ac-list .acc')[2].textContent"))
        self.page.click("#ac-list button[data-email='two@example.test']")
        self.assertEqual(self.switched, [])
        self.assertIn("here", self.js("document.querySelector('#ac-list button.sure').textContent"))
        self.page.click("#ac-list button[data-email='two@example.test']")
        self.page.wait_for_timeout(300)
        self.assertEqual(self.switched, [{"email": "two@example.test"}])


@skip_unless_browser
class PhoneAccountPill(AccountPill):
    device = IPHONE
