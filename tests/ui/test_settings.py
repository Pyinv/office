"""Settings dialog: preferences store, theme, terminal text size, notifications, the agents row."""
from ._base import (BrowserCase, IPHONE, DESKTOP, OFFICE_URL, PLAN_EMPTY, PLAN_RESTORE, PLAN_UPDATE, PLAN_RUNNING,
                    PLAN_PARTIAL, skip_unless_browser)


@skip_unless_browser
class PrefsStore(BrowserCase):
    device = IPHONE

    def test_defaults_when_nothing_stored(self):
        self.open_floor()
        self.assertEqual(self.js("pref('theme')"), "system")
        self.assertEqual(self.js("termFontSize()"), 11)          # touch default
        self.assertTrue(self.js("pref('notify')"))

    def test_corrupt_json_falls_back_to_defaults(self):
        self.page.add_init_script("localStorage.setItem('office.prefs', '{not json')")
        self.open_floor()
        self.assertEqual(self.js("pref('theme')"), "system")
        self.assertEqual(self.js("termFontSize()"), 11)

    def test_legacy_keys_are_folded_in_once(self):
        self.page.add_init_script("localStorage.setItem('office-notify','0'); localStorage.setItem('filters','1')")
        self.open_floor()
        self.assertFalse(self.js("pref('notify')"))
        self.assertFalse(self.js("document.getElementById('bell').classList.contains('on')"))
        self.assertTrue(self.js("document.body.classList.contains('filters-open')"))

    def test_blocked_storage_still_renders(self):
        self.page.add_init_script("""Object.defineProperty(window, 'localStorage', {get(){ throw new Error('blocked'); }});""")
        self.open_floor()
        self.open_settings()
        self.assertEqual(self.js("document.getElementById('st-fval').textContent"), "11")


@skip_unless_browser
class Theme(BrowserCase):
    device = IPHONE

    def test_system_follows_the_os_and_the_choice_persists(self):
        self.page.emulate_media(color_scheme="dark")
        self.open_floor()
        self.assertEqual(self.js("document.documentElement.dataset.theme"), "dark")
        self.page.emulate_media(color_scheme="light")
        self.page.wait_for_timeout(100)
        self.assertEqual(self.js("document.documentElement.dataset.theme"), "light")
        self.open_settings()
        self.assertIn("(light)", self.js("document.getElementById('st-theme-s').textContent"))
        self.assertIn("terminal stays dark", self.js("document.getElementById('st-theme-s').textContent"))
        self.js("document.querySelector('input[name=theme][value=dark]').click()")
        self.assertEqual(self.js("document.documentElement.dataset.theme"), "dark")
        self.assertNotIn("terminal stays dark", self.js("document.getElementById('st-theme-s').textContent"))
        self.page.reload(); self.page.wait_for_selector(".desk")
        self.assertEqual(self.js("document.documentElement.dataset.theme"), "dark")
        self.assertEqual(self.prefs()["theme"], "dark")

    def test_theme_is_set_before_the_page_paints(self):
        self.page.add_init_script("localStorage.setItem('office.prefs', JSON.stringify({theme:'light'}))")
        self.page.emulate_media(color_scheme="dark")
        self.page.goto(OFFICE_URL + "/", wait_until="commit")
        self.page.wait_for_selector("body")
        self.assertEqual(self.js("document.documentElement.dataset.theme"), "light")


@skip_unless_browser
class TerminalTextSize(BrowserCase):
    device = IPHONE

    def test_stepper_clamps_and_persists(self):
        self.open_floor(); self.open_settings()
        for _ in range(12):
            self.js("document.getElementById('st-fplus').click()")
        self.assertEqual(self.js("document.getElementById('st-fval').textContent"), "20")
        self.assertTrue(self.js("document.getElementById('st-fplus').disabled"))
        for _ in range(15):
            self.js("document.getElementById('st-fminus').click()")
        self.assertEqual(self.js("document.getElementById('st-fval').textContent"), "8")
        self.assertTrue(self.js("document.getElementById('st-fminus').disabled"))
        self.assertEqual(self.js("document.getElementById('st-fsample').style.fontSize"), "8px")
        self.page.reload(); self.page.wait_for_selector(".desk")
        self.assertEqual(self.js("termFontSize()"), 8)

    def test_change_reaches_the_open_terminal_and_the_aa_key_opens_settings(self):
        self.open_floor()
        session = self.js("document.querySelector('.desk').dataset.session")
        self.page.goto(self.page.url.rstrip('/') + "/" + session)
        self.page.wait_for_selector("#termframe")
        self.page.wait_for_function("(()=>{ try{ return !!document.getElementById('termframe').contentWindow.term; }catch(e){ return false; } })()", timeout=15000)
        self.page.wait_for_timeout(1500)
        self.assertEqual(self.js("document.getElementById('termframe').contentWindow.term.options.fontSize"), 11)
        self.js("document.querySelector('.keybar button[data-key=font]').click()")
        self.page.wait_for_timeout(200)
        self.assertTrue(self.js("document.getElementById('settingsdlg').open"))
        self.js("document.getElementById('st-fplus').click()")
        self.page.wait_for_timeout(300)
        self.assertEqual(self.js("document.getElementById('termframe').contentWindow.term.options.fontSize"), 12)
        # the terminal's own chrome keeps the dark tokens whatever the page theme
        self.js("document.querySelector('input[name=theme][value=light]').click()")
        self.assertEqual(self.js("getComputedStyle(document.querySelector('.mbar')).backgroundColor"), "rgb(28, 30, 29)")


@skip_unless_browser
class DesktopDefaults(BrowserCase):
    device = DESKTOP
    plan = PLAN_UPDATE

    def test_desktop_font_default_and_row_order(self):
        self.open_floor()
        self.assertEqual(self.js("termFontSize()"), 14)
        self.assertEqual(self.js("[...document.querySelectorAll('.titlerow > button')].map(b=>b.id).join(' ')"),
                         "restartbtn bell settingsbtn")
        self.assertEqual(self.js("document.getElementById('restartbtn').textContent"), "⟳ Update 1 agent")
        # the update lives behind the gear on the laptop too: the button hides, the gear lights up
        self.assertEqual(self.js("getComputedStyle(document.getElementById('restartbtn')).display"), "none")
        self.assertNotEqual(self.js("getComputedStyle(document.getElementById('settingsbtn')).boxShadow"), "none")
        # the filter chips fold behind the Filters button on the laptop as well
        self.assertNotEqual(self.js("getComputedStyle(document.getElementById('filtertoggle')).display"), "none")
        self.assertEqual(self.js("getComputedStyle(document.querySelector('.filtercol')).display"), "none")
        self.open_settings()
        self.assertIsNone(self.js("document.getElementById('st-shortcut-row')"))


@skip_unless_browser
class Notifications(BrowserCase):
    device = IPHONE
    permissions = ("notifications",)

    def test_toggle_and_bell_share_one_state(self):
        self.open_floor(); self.open_settings()
        self.assertTrue(self.js("document.getElementById('st-notify').checked"))
        # the bell mutes: the Settings toggle follows, the pref and the legacy key are written
        self.js("document.getElementById('bell').click()")
        self.page.wait_for_timeout(100)
        self.assertFalse(self.js("document.getElementById('st-notify').checked"))
        self.assertFalse(self.js("document.getElementById('bell').classList.contains('on')"))
        self.assertFalse(self.prefs()["notify"])
        self.assertEqual(self.js("localStorage.getItem('office-notify')"), "0")     # legacy key kept for one release
        # the toggle un-mutes: the bell follows (only where the browser really grants the permission —
        # headless Chromium keeps it "denied", which disables the toggle by design)
        if self.js("Notification.permission") == "granted":
            self.js("document.getElementById('st-notify').click()")
            self.page.wait_for_timeout(100)
            self.assertTrue(self.js("document.getElementById('bell').classList.contains('on')"))
        else:
            self.assertTrue(self.js("document.getElementById('st-notify').disabled"))


@skip_unless_browser
class NotificationsDenied(BrowserCase):
    device = IPHONE

    def test_denied_permission_disables_the_toggle(self):
        # headless Chromium reports "denied" when nothing was granted
        self.open_floor(); self.open_settings()
        if self.js("Notification.permission") == "denied":
            self.assertTrue(self.js("document.getElementById('st-notify').disabled"))
            self.assertEqual(self.js("document.getElementById('st-notify-s').textContent"), "Blocked in the browser settings")


@skip_unless_browser
class AgentsRow(BrowserCase):
    device = IPHONE

    def row(self):
        return (self.js("document.getElementById('st-agents-l').textContent"),
                self.js("document.getElementById('settingsdlg').classList.contains('pending')"))

    def test_idle(self):
        self.open_floor(); self.open_settings()
        self.assertEqual(self.row(), ("All agents are up to date", False))
        self.assertFalse(self.js("document.getElementById('restorebar').classList.contains('show')"))

    def test_restore_pending_shows_banner_and_row_first(self):
        self.stub_plan(PLAN_RESTORE); self.open_floor()
        self.assertTrue(self.js("document.getElementById('restorebar').classList.contains('show')"))
        self.assertEqual(self.js("document.getElementById('rb-t').textContent"), "The Tower restarted — 2 agents to bring back")
        self.assertTrue(self.js("document.getElementById('settingsbtn').classList.contains('pending')"))
        self.open_settings()
        self.assertEqual(self.row(), ("Restore 2 agents →", True))
        self.assertEqual(self.js("getComputedStyle(document.getElementById('st-agents')).order"), "0")
        self.js("document.getElementById('st-agents').click()")
        self.page.wait_for_timeout(300)
        self.assertFalse(self.js("document.getElementById('settingsdlg').open"))   # one dialog at a time
        self.assertTrue(self.js("document.getElementById('restartdlg').open"))
        self.assertEqual(self.js("document.getElementById('rs-title').textContent"), "Bring the agents back")

    def test_banner_opens_the_restart_dialog(self):
        self.stub_plan(PLAN_RESTORE); self.open_floor()
        self.js("document.getElementById('rb-go').click()")
        self.page.wait_for_timeout(300)
        self.assertTrue(self.js("document.getElementById('restartdlg').open"))

    def test_running_partial_and_error(self):
        self.stub_plan(PLAN_RUNNING); self.open_floor(); self.open_settings()
        self.assertTrue(self.row()[0].startswith("Restarting "))
        self.js("document.getElementById('settingsdlg').close()")
        self.stub_plan(PLAN_PARTIAL); self.open_settings(); self.page.wait_for_timeout(300)
        self.assertEqual(self.row(), ("1 back · 1 need you →", True))
        self.js("document.getElementById('settingsdlg').close()")
        self.stub_plan(PLAN_EMPTY, 500); self.open_settings(); self.page.wait_for_timeout(300)
        self.assertEqual(self.js("document.getElementById('st-agents-l').textContent"), "Can't reach the server")
        self.assertTrue(self.js("document.getElementById('st-agents').classList.contains('err')"))
        self.stub_plan(PLAN_EMPTY)                      # retry works
        self.js("document.getElementById('st-agents').click()"); self.page.wait_for_timeout(300)
        self.assertEqual(self.row()[0], "All agents are up to date")


@skip_unless_browser
class FirstOpenHint(BrowserCase):
    device = IPHONE

    def test_shown_once(self):
        self.open_floor(); self.open_settings()
        self.assertFalse(self.js("document.getElementById('st-sub').hidden"))
        self.js("document.getElementById('settingsdlg').close()")
        self.open_settings()
        self.assertTrue(self.js("document.getElementById('st-sub').hidden"))
