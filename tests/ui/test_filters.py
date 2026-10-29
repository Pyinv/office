"""Regressions around the phone header: the Filters pill still folds the chips out, the gear glows
only on the phone, and nothing that used to live in the fold-out is left behind there."""
from ._base import BrowserCase, IPHONE, PLAN_RESTORE, skip_unless_browser


@skip_unless_browser
class FiltersPill(BrowserCase):
    device = IPHONE
    plan = PLAN_RESTORE

    def test_pill_toggles_the_chips_and_remembers(self):
        self.open_floor()
        self.assertEqual(self.js("document.getElementById('filtertoggle').getAttribute('aria-label')"), "Filters")
        self.assertFalse(self.js("document.body.classList.contains('filters-open')"))
        self.assertEqual(self.js("getComputedStyle(document.querySelector('.filtercol')).display"), "none")
        self.js("document.getElementById('filtertoggle').click()")
        self.assertTrue(self.js("document.body.classList.contains('filters-open')"))
        self.assertEqual(self.js("document.getElementById('filtertoggle').getAttribute('aria-expanded')"), "true")
        self.assertEqual(self.js("getComputedStyle(document.querySelector('.filtercol')).display"), "flex")
        self.assertTrue(self.prefs()["filters"])
        self.page.reload(); self.page.wait_for_selector(".desk")
        self.assertTrue(self.js("document.body.classList.contains('filters-open')"))

    def test_active_filter_count_on_the_pill(self):
        self.open_floor()
        self.assertTrue(self.js("document.getElementById('filtercount').hidden"))
        self.js("document.getElementById('filtertoggle').click()")
        self.js("[...document.querySelectorAll('#chips .chip')].find(c=>!c.classList.contains('on')).click()")
        self.page.wait_for_timeout(200)
        self.assertEqual(self.js("document.getElementById('filtercount').textContent"), "1")
        self.assertTrue(self.js("document.getElementById('filtertoggle').classList.contains('active')"))

    def test_fold_out_holds_only_the_chips_now(self):
        self.open_floor()
        self.assertFalse(self.js("!!document.querySelector('.filtercol #restartbtn, .filtercol .navlink')"))
        self.assertEqual(self.js("getComputedStyle(document.getElementById('restartbtn')).display"), "none")

    def test_gear_is_a_real_touch_target_and_glows_while_pending(self):
        self.open_floor()
        w, h = self.js("(r=>[r.width,r.height])(document.getElementById('settingsbtn').getBoundingClientRect())")
        self.assertGreaterEqual(min(w, h), 44)
        self.assertTrue(self.js("document.getElementById('settingsbtn').classList.contains('pending')"))
        self.assertNotEqual(self.js("getComputedStyle(document.getElementById('settingsbtn')).boxShadow"), "none")
        w, h = self.js("(r=>[r.width,r.height])(document.getElementById('filtertoggle').getBoundingClientRect())")
        self.assertGreaterEqual(h, 44)
