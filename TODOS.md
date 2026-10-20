# TODOS

## iPhone app

### Push taps land inside the native app

**What:** Register a URL scheme (or Universal Link) in `ios/` and point the ntfy click target at it, so a notification tap opens the agent's terminal in the Office app instead of the browser.

**Why:** The native shell is where answering agents is comfortable (no keyboard accessory bar), but every push still opens Safari or the home-screen web app, so the app is not the entry point it should be.

**Context:** `server.py` builds the ntfy message with a `Click` header pointing at `/<session>`. The app (`ios/Office`) opens whatever address it was given at first launch and shows pages in its own web view. Add a `CFBundleURLTypes` scheme (e.g. `office://<session>`) in `ios/project.yml` → Info.plist, handle it in `SceneDelegate` (`scene(_:openURLContexts:)` → load `<base>/<session>`), and either send that scheme as the click target when the push goes to iOS, or keep the https link and register it as a Universal Link (needs an `apple-app-site-association` file served from the Office host). Scheme is the smaller change; Universal Link keeps browser taps working too. Requires a rebuild on the Mac.

**Effort:** S
**Priority:** P3
**Depends on:** None

## Settings

### Terminal follows the light theme

**What:** Push a light xterm palette into the terminal frame when the page theme is light.

**Why:** In light mode the terminal is a dark block inside a light page.

**Context:** Deferred in the settings-screen review: Claude Code paints its own colours per its `/theme` setting, so a light terminal under an agent on the dark theme gives low-contrast text, and every theme switch repaints every pane. Revisit if the agents standardise on a light Claude Code theme. The mechanism is the same as the font pin in `app.html` (`term.options.theme`).

**Effort:** S
**Priority:** P4
**Depends on:** Settings screen (theme setting)

## Completed
