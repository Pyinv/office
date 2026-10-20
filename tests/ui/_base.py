"""Browser tests for the Office client (app.html), driven by Playwright.

They run against a live Office: set OFFICE_URL (e.g. http://<tailscale-ip>:8899/) and have
`pip install -r tests/requirements-ui.txt && playwright install chromium` done once. Without
either, every test here is skipped, so the plain `python -m unittest discover -s tests` stays
green on a machine (or CI) that has no browser.
"""
import json
import os
import unittest

OFFICE_URL = os.environ.get("OFFICE_URL", "").rstrip("/")
try:
    from playwright.sync_api import sync_playwright
    HAVE_PW = True
except Exception:  # pragma: no cover - the import is the check
    HAVE_PW = False

skip_unless_browser = unittest.skipUnless(
    HAVE_PW and OFFICE_URL, "needs OFFICE_URL and the playwright package (see tests/ui/_base.py)")

IPHONE = dict(viewport={"width": 390, "height": 844}, device_scale_factor=3, is_mobile=True, has_touch=True,
              user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
                         "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
DESKTOP = dict(viewport={"width": 1300, "height": 900})

# a restart plan the server would return — stubbed so the tests never depend on the live Tower
PLAN_EMPTY = {"restore": [], "update": [], "skip": [], "job": {"running": False}}
PLAN_RESTORE = {"restore": [{"session": "a-b"}, {"session": "c-d"}], "update": [], "skip": [], "job": {"running": False}}
PLAN_UPDATE = {"restore": [], "update": [{"session": "x-y", "person": "X", "mode": "auto"}], "skip": [], "job": {"running": False}}
PLAN_RUNNING = {"restore": [], "update": [{"session": "x-y"}], "skip": [],
                "job": {"running": True, "log": ["✓ a-b restarted (auto)", "✓ c-d restarted (auto)"]}}
PLAN_PARTIAL = {"restore": [], "update": [], "skip": [],
                "job": {"running": False, "done": 1, "log": ["✓ a-b restarted", "✗ c-d: claude did not come back"]}}


class BrowserCase(unittest.TestCase):
    """One Chromium per test class; a fresh context (fresh localStorage) per test."""

    device = DESKTOP
    plan = PLAN_EMPTY          # what /api/restart answers; None = pass through to the server
    plan_status = 200
    permissions = ()

    @classmethod
    def setUpClass(cls):
        if not (HAVE_PW and OFFICE_URL):
            raise unittest.SkipTest("needs OFFICE_URL and playwright")
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()

    def setUp(self):
        self.ctx = self.browser.new_context(**self.device)
        if self.permissions:
            self.ctx.grant_permissions(list(self.permissions), origin=OFFICE_URL)
        self.errors = []
        self.page = self.ctx.new_page()
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        if self.plan is not None:
            self.stub_plan(self.plan, self.plan_status)

    def tearDown(self):
        self.ctx.close()
        self.assertEqual(self.errors, [], "page errors")

    def stub_plan(self, plan, status=200):
        def handle(route):
            if status == 200:
                route.fulfill(status=200, content_type="application/json", body=json.dumps(plan))
            else:
                route.fulfill(status=status, body="x")
        self.page.route("**/api/restart", handle)

    def open_floor(self):
        self.page.goto(OFFICE_URL + "/")
        self.page.wait_for_selector(".card")
        self.page.wait_for_timeout(500)       # the first plan() poll

    def js(self, expr):
        return self.page.evaluate(expr)

    def prefs(self):
        raw = self.js("localStorage.getItem('office.prefs')")
        return json.loads(raw) if raw else {}

    def open_settings(self):
        self.js("document.getElementById('settingsbtn').click()")
        self.page.wait_for_timeout(300)
        self.assertTrue(self.js("document.getElementById('settingsdlg').open"))
