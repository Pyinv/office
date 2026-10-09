# Office — a live wall for your tmux Claude Code sessions

A self-hosted web dashboard that shows every tmux-based [Claude Code](https://claude.com/claude-code)
session as a named "team" you can glance at, answer, and manage — from your phone or laptop —
over your private [Tailscale](https://tailscale.com) network. Nothing is exposed to the public
internet; your tailnet is the trust boundary.

It reads tmux + Claude Code transcripts to tell, per session, whether Claude is **working**,
**waiting on you**, or **finished (your move)** — so a wall of parallel sessions becomes glanceable.

## Features

- **Live floor** — one card per tmux session (who/what/state), most-recently-active first.
- **Answer inline** — reply to a session; it's typed in via `tmux send-keys`.
- **Full terminal** — a real interactive terminal per session (via [ttyd](https://github.com/tsl0922/ttyd)), with seamless select-to-copy and an open-in-new-tab button. Each terminal has its own address (`/<session-name>`), so a refresh, a bookmark or a shared link lands straight in it.
- **Phone terminal** — on touch devices, swipe to scroll and a key bar above the keyboard with esc, tab, arrows, enter and the Ctrl keys (^B works as the tmux prefix). Paste uses the clipboard when the browser allows it and falls back to a paste box when it doesn't.
- **Organize** — pin, categorize (clients / ventures / personal), and attach a **focus note** (main task + sub-tasks) that stays pinned to the card.
- **Manage** — rename a session (tmux and its Office state move together), kill it, or spawn a new one from a form (person, project, folder, category) confined to your dev root. Sessions that died or were killed stay in history, so "+ New" can bring one back by name with its old folder and category — and resume its last Claude conversation. When Claude exits inside a session, the pane drops to a shell instead of closing.
- **Secretary** — a chief-of-staff panel above the floor: type *"ask Lena to run the risk file, then have Mara draft the section"* and it finds those sessions and relays the instruction (real `send-keys`).
- **Usage dashboard** — tokens, spend, and rate-limit meters at `/status/`.
- **Phone push** — optional: when a session turns to you with a question, the server posts "*Name* needs you" plus the question to an [ntfy](https://ntfy.sh) topic, with a tap target straight into that terminal. Point it at your own ntfy server and nothing leaves your network (iOS still gets its wake-up through ntfy.sh, carrying only the topic name and a message id). See `OFFICE_NTFY_*` in `.env.example`.

## How it works

| File | Role |
|------|------|
| `server.py` | Python stdlib HTTP server: serves the app + JSON API, reverse-proxies the terminal. Binds the Tailscale IP only; refuses to start without one. |
| `team.py` | The detector: reads tmux panes + `~/.claude/projects/*/*.jsonl` transcripts and classifies each session. |
| `app.html` | The entire client — one file, inline CSS + JS. |
| `status/` | Usage dashboard: `build.py` aggregates transcripts → `data.json`; `index.html` renders it. |
| `serve.sh` / `serve-term.sh` | Launch the web server / the ttyd terminal. |
| `keepalive.sh` | Cron watchdog — keeps both running (tailnet-only), self-heals on reboot/crash. |
| `reload` | Hot-reload the running server in place (SIGHUP re-exec). |

Person + project come from the tmux session name (`lena-payments` → *Lena / payments*). tmux is the single
source of truth for identity — there is no roster file.

## Requirements

- **Python 3.10+** — standard library only, no `pip` dependencies.
- **tmux** — your sessions live in tmux.
- **ttyd** — for the interactive terminal: `sudo apt install -y ttyd`.
- **Tailscale** — the app binds your tailnet IP and is reachable only from your own devices.

## Setup

1. **Configure** (all personal/instance settings live in `.env`, which is gitignored):
   ```sh
   cp .env.example .env
   $EDITOR .env          # set OFFICE_TITLE; optionally OFFICE_EMAIL / OFFICE_TAILNET_HOST
   ```
   Optional: create `emoji.local.json` — a `{ "project-keyword": "emoji" }` map for per-project
   avatars, e.g. `{ "web": "🌐", "api": "🔌" }`.

2. **Tailscale up:** `tailscale up`. Optionally front it with a clean HTTPS URL (valid cert, no port):
   ```sh
   tailscale serve --bg <port>      # serves https://<machine>.<tailnet>.ts.net/
   ```

3. **Run it** (both bind tailnet/localhost only):
   ```sh
   ./serve.sh &          # web + API  (binds your Tailscale IP)
   ./serve-term.sh &     # ttyd terminal (private unix socket, proxied at /terminal)
   ```
   Or let the watchdog run them from cron so they survive reboots and crashes:
   ```cron
   @reboot        /path/to/office/keepalive.sh
   * * * * *      /path/to/office/keepalive.sh
   ```

4. Open `http://<machine>.<tailnet>.ts.net:<port>/` (or your `tailscale serve` HTTPS URL) from any
   device on your tailnet.

## Configuration — `.env`

| Key | Default | Purpose |
|-----|---------|---------|
| `OFFICE_TITLE` | `Office` | Header + browser-tab title |
| `OFFICE_EMAIL` | *(empty)* | Optional, shown on the status page |
| `OFFICE_TAILNET_HOST` | *(auto)* | Optional; the machine's tailnet name is auto-detected from `tailscale status` |
| `OFFICE_DEV_ROOT` | `~/develop` | Where "+ New" may create project folders |
| `OFFICE_CLAUDE_BIN` | `$(which claude)` | Path to the `claude` binary |
| `OFFICE_PORT` | `8899` | Port for the web server |
| `OFFICE_STATUS_URL` | `/status/` | Header "Usage &amp; limits" link; empty hides it |
| `OFFICE_FILES_URL` | *(off)* | Header "Files" link, e.g. a `/dufs/` mount; empty hides it |
| `OFFICE_SECRETARY_NAME` | `Secretary` | Display name for the dispatcher panel |
| `OFFICE_DEMO` | *(off)* | `1` (or a path) → serve `demo.json` fixtures instead of live tmux |

## Demo mode

Want a populated floor without wiring up real tmux sessions — for a screenshot, a talk, or
just to try it? Turn on demo mode; it serves the fictional team in [`demo.json`](demo.json):

```sh
OFFICE_DEMO=1 ./serve.sh
```

Set `OFFICE_DEMO=1` in `.env` to make it stick, or point it at your own fixture
(`OFFICE_DEMO=/path/to/my-demo.json`). `demo.json` is just a list of session cards — copy it and
edit the names, messages, and `group` (`clients` / `ventures` / `personal`). An `ago_s` field on a
card sets "how long ago" it last replied, so the demo always looks live. All bundled demo data is
fictional.

## Secretary

The **Secretary** panel sits above the floor — your chief of staff. Type a plain-language
instruction and it routes it to the right session:

> ask Lena to run the risk file, then have Mara draft the key-usage section

It matches the names against the live team, relays a polite instruction (`Please run the risk
file`) into that session via `tmux send-keys`, and confirms who it went to. In **demo mode** there's
no real tmux, so it simulates the dispatch and the target card visibly reacts on the floor. Rename
the assistant with `OFFICE_SECRETARY_NAME`.

## Security model

- **Tailnet-only.** The server binds your Tailscale IP and *refuses to start without one*; never
  the LAN or the public internet. ttyd listens on a private **unix socket** (no TCP port) and is
  reverse-proxied at `/terminal`. A browser cannot open a websocket to a unix socket, so no foreign
  page can reach the shell.
- **CSRF Origin allowlist** gates every state-changing `POST` and the terminal websocket; the
  machine's own MagicDNS name is detected live, so a tailnet rename self-heals.
- **Static serving is allowlisted** — only the dashboard's own assets are served as files;
  `.env`, runtime state, source, and notes are never web-served.
- **No app-layer auth, by design.** Anyone on your tailnet can drive the sessions, and the terminal
  is a real shell. Treat tailnet access as full trust: keep your tailnet private and don't share the
  node casually.

## What stays local (gitignored)

`.env`, `emoji.local.json`, and the runtime state (`groups.json`, `pins.json`, `tasks.json`,
`history.json`, `status/data.json`) hold your own config and session data — they are gitignored and never committed.

## Tests

Pure-stdlib, no dependencies and no tmux required — the detector's helpers are tested
against synthetic transcripts in a temp dir:

```sh
python -m unittest discover -s tests -v
```

CI runs the same suite on Python 3.9–3.13 for every push and pull request
(see [`.github/workflows/ci.yml`](.github/workflows/ci.yml)).

## Versioning

This project follows [semantic versioning](https://semver.org). The current version is
exposed as `server.__version__` and in the `/api/floor` response. Releases are git-tagged
(`vMAJOR.MINOR.PATCH`).

## License

MIT — see [`LICENSE`](LICENSE).
