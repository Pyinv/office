#!/usr/bin/env python3
"""office/server.py — the office, live, on the tailnet.

Serves:
  GET  /              -> app.html (the office floor)
  GET  /api/floor     -> JSON snapshot of all sessions
  POST /api/say       -> {session, text}  -> tmux send-keys into that session
  GET  /status/, /doc/ -> the existing static dashboard + docs (unchanged)

Binds to the Tailscale IP ONLY. Refuses to start without one, so the reply
endpoint (which can type into live Claude sessions) is never exposed to the LAN
or the internet — same trust boundary as sitting at the tower.
"""
import json, subprocess, sys, os, time, importlib, signal, socket, threading, shutil, re, tempfile, shlex
from urllib.parse import urlparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

__version__ = "0.0.2"

# ── config: derived generically so the repo carries no user/host specifics.
# A gitignored .env (see .env.example) supplies instance details (title, email,
# tailnet host) and optional path overrides.
ROOT = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~")

def _load_env(path):
    env = {}
    try:
        with open(path) as f:
            for ln in f:
                ln = ln.strip()
                if not ln or ln.startswith("#") or "=" not in ln:
                    continue
                k, v = ln.split("=", 1)
                k, v = k.strip(), v.strip()
                if v[:1] in ("'", '"'):        # quoted: take the quoted span, ignore any trailing comment
                    q = v[0]; end = v.find(q, 1)
                    v = v[1:end] if end != -1 else v[1:]
                else:                          # bare: drop an inline " # comment"
                    v = v.split(" #", 1)[0].strip()
                env[k] = v
    except OSError:
        pass
    return env

_ENV = _load_env(os.path.join(ROOT, ".env"))
for _k, _v in _ENV.items():   # export .env process-wide so team.py (OFFICE_DEMO) + subprocesses see it
    os.environ.setdefault(_k, _v)
def cfg(key, default=""):
    # explicit "" (e.g. OFFICE_STATUS_URL="" to hide a link) must win over the default
    if key in os.environ: return os.environ[key]
    if key in _ENV:       return _ENV[key]
    return default

PORT       = int(cfg("OFFICE_PORT", "8899"))
TITLE      = cfg("OFFICE_TITLE", "Office")
EMAIL      = cfg("OFFICE_EMAIL", "")
DEV_ROOT   = cfg("OFFICE_DEV_ROOT", os.path.join(HOME, "develop"))
SEC_CWD    = os.path.join(tempfile.gettempdir(), "office-secretary")   # secretary runs here, not in a project
CLAUDE_BIN = cfg("OFFICE_CLAUDE_BIN", shutil.which("claude") or os.path.join(HOME, ".local", "bin", "claude"))
STATUS_URL = cfg("OFFICE_STATUS_URL", "/status/")   # header "Usage & limits" link; "" hides it
FILES_URL  = cfg("OFFICE_FILES_URL", "")            # header "Files" link (e.g. a dufs mount); "" hides it
SECRETARY  = cfg("OFFICE_SECRETARY_NAME", "Secretary")   # the dispatcher's display name
SEC_MODEL  = cfg("OFFICE_SECRETARY_MODEL", "claude-haiku-4-5-20251001")  # model the secretary reasons with

# ttyd listens on a private UNIX SOCKET (not a TCP port) and is reached only via the
# /terminal reverse proxy below. A browser cannot open a websocket to a unix socket,
# so a foreign webpage can never reach the shell directly.
TTYD_SOCK = os.path.join(ROOT, "ttyd.sock")

# Origin allowlist. Browsers cannot forge the Origin header from JS, so an exact
# host check blocks cross-site POST (CSRF) and cross-origin terminal websockets.
# main() also adds the bound tailnet IP AND the machine's live tailnet DNS name at
# startup, so this stays correct across a tailnet rename. OFFICE_TAILNET_HOST (.env)
# adds more: a fallback for boot before `tailscale status` is readable, or any other
# name a reverse proxy serves the app under (comma-separated).
ALLOWED_HOSTS = {"localhost", "127.0.0.1"}
for _h in cfg("OFFICE_TAILNET_HOST").split(","):
    if _h.strip():
        ALLOWED_HOSTS.add(_h.strip())

# Only these exact paths are servable as raw static files by the fallback handler.
# Everything else in ROOT (.env, *.json state, source, doc/) must NEVER be web-served.
# Exact-match — immune to ../ and %2e traversal that a prefix check would allow.
STATIC_OK = {"/status/chart.umd.min.js", "/status/data.json", "/manifest.json",
             "/icons/icon-180.png", "/icons/icon-192.png", "/icons/icon-512.png"}
_SEC_OVERLAY = {}   # demo: session -> {you,state,verb,ts} set by the Secretary, shown live on the floor

# ── phone push (ntfy) ──────────────────────────────────────────────────────
# When a session turns to you with a question, post it to a private ntfy server so the
# phone buzzes: "Ahmed needs you" + the question + a tap target straight into that
# terminal. Nothing is sent without OFFICE_NTFY_URL/TOPIC/TOKEN; the body carries the real
# text because the server is yours (see README: ntfy.sh only ever relays a wake-up ping).
NTFY_URL    = cfg("OFFICE_NTFY_URL").rstrip("/")
NTFY_TOPIC  = cfg("OFFICE_NTFY_TOPIC")
NTFY_TOKEN  = cfg("OFFICE_NTFY_TOKEN")
NTFY_EVENTS = {e.strip() for e in cfg("OFFICE_NTFY_EVENTS", "waiting").split(",") if e.strip()}
OFFICE_URL  = cfg("OFFICE_URL").rstrip("/")       # deep-link base for the tap target
_NOTIFIED = {}      # session -> key of the last state we pushed, so one question = one push
_NOTIFY_LOCK = threading.Lock()
_NOTIFY_SEEDED = False   # the first snapshot after a (re)start only records state: no burst of old questions

def _ntfy_post(title, body, click, tags, priority="default"):
    import urllib.request
    req = urllib.request.Request(NTFY_URL + "/" + NTFY_TOPIC, data=body.encode("utf-8"), method="POST")
    req.add_header("Authorization", "Bearer " + NTFY_TOKEN)
    req.add_header("Title", title.encode("utf-8").decode("latin-1", "replace"))   # header-safe
    req.add_header("Tags", tags)
    req.add_header("Priority", priority)
    if click:
        req.add_header("Click", click)
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as e:
        sys.stderr.write(f"office/ntfy: {e}\n")

def notify_changes(snap):
    """Push once per new question (or finish), never on every poll. Runs after each
    floor snapshot; the HTTP call itself goes to a thread so polls stay fast."""
    global _NOTIFY_SEEDED
    if not (NTFY_URL and NTFY_TOPIC and NTFY_TOKEN):
        return
    with _NOTIFY_LOCK:
        quiet = not _NOTIFY_SEEDED; _NOTIFY_SEEDED = True
        live = set()
        for m in snap:
            s = m["session"]; live.add(s)
            who = m.get("person") or m.get("project") or s
            st = m.get("state")
            click = f"{OFFICE_URL}/{s}" if OFFICE_URL else ""
            if st == "waiting" and "waiting" in NTFY_EVENTS and (m.get("ask") or "").strip():
                ask = re.sub(r"\s+", " ", m["ask"]).strip()
                key = "waiting:" + ask[:200]
                if _NOTIFIED.get(s) != key:
                    _NOTIFIED[s] = key
                    if not quiet: threading.Thread(target=_ntfy_post, args=(f"{who} needs you", ask[:400], click, "raising_hand", "high"), daemon=True).start()
            elif st == "review" and "review" in NTFY_EVENTS and str(_NOTIFIED.get(s, "")).startswith("working"):
                _NOTIFIED[s] = "review"
                if not quiet: threading.Thread(target=_ntfy_post, args=(f"{who} is done", (m.get("full") or "")[-300:].strip() or "your move", click, "white_check_mark"), daemon=True).start()
            elif st == "working":
                if not str(_NOTIFIED.get(s, "")).startswith("working"):
                    _NOTIFIED[s] = "working"
            elif st not in ("waiting", "review"):
                _NOTIFIED.pop(s, None)
        for s in list(_NOTIFIED):
            if s not in live:
                _NOTIFIED.pop(s, None)
_WATCHES = []       # follow-ups: [{session,person,act,prev,ts}] — fire when a session finishes
_WATCHES_LOCK = threading.Lock()   # serialize watch fire+remove across concurrent /api/floor polls

def emoji_json():
    # Per-instance project->emoji map (gitignored emoji.local.json). Returned as a
    # JSON string to inject into app.html; "{}" if absent so the generic defaults apply.
    try:
        with open(os.path.join(ROOT, "emoji.local.json"), encoding="utf-8") as f:
            return json.dumps(json.load(f))
    except Exception:
        return "{}"

sys.path.insert(0, ROOT)
import team  # noqa: E402

# SIGHUP -> re-exec self with fresh code (server.py + team.py + app.html).
# The listening socket has close-on-exec set by default (PEP 446), so the port
# frees on exec and main() rebinds it. Lets `office/reload` update the running
# server in place without a manual restart.
def _reload(*_):
    for k in _ENV:                 # drop what WE exported from .env, so the new process re-reads
        os.environ.pop(k, None)    # the file; real environment overrides are not ours to drop
    os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])
signal.signal(signal.SIGHUP, _reload)

def tailscale_ip():
    for _ in range(60):
        out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True).stdout.strip()
        ip = out.splitlines()[0].strip() if out else ""
        if ip:
            return ip
        time.sleep(5)
    return ""

def tailscale_dnsname():
    # The machine's own MagicDNS name (e.g. machine.<tailnet>.ts.net),
    # read live so an Origin allowlist survives a tailnet rename.
    try:
        out = subprocess.run(["tailscale", "status", "--json"],
                             capture_output=True, text=True, timeout=5).stdout
        name = (json.loads(out).get("Self") or {}).get("DNSName", "").rstrip(".")
        return name
    except Exception:
        return ""

def live_sessions():
    return set(subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True, text=True).stdout.split())

# ── categories you assign (clients / ventures / personal) ────────────────
import re as _re
GROUPS_FILE = os.path.join(ROOT, "groups.json")
VALID_GROUPS = ("clients", "ventures", "personal")

def load_groups():
    try:
        with open(GROUPS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def save_groups(g):
    tmp = GROUPS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(g, f, indent=2, sort_keys=True)
    os.replace(tmp, GROUPS_FILE)

def _login_shell():
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_shell or "/bin/sh"
    except Exception:
        return os.environ.get("SHELL") or "/bin/sh"

def safe_session(name):
    # tmux session names can't contain . or : ; keep it simple + shell-safe
    return _re.sub(r'[^a-zA-Z0-9_-]', '-', (name or "").strip())[:48].strip("-")

# ── pinned sessions (kept at the top of the board) ───────────────────────
PINS_FILE = os.path.join(ROOT, "pins.json")

def load_pins():
    try:
        with open(PINS_FILE) as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_pins(p):
    tmp = PINS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(sorted(p), f, indent=2)
    os.replace(tmp, PINS_FILE)

# ── per-session focus note (main task + sub-tasks, pinned atop the card) ──
# { "<session-name>": "<note text>" } — first line = main task, rest = sub-tasks.
# Edited by the user as a reminder of what a session set out to do.
TASKS_FILE = os.path.join(ROOT, "tasks.json")

# ── session history: every session ever seen on the floor, with its folder and
# category, so one that died (crash, reboot, kill) can be brought back from "+ New".
HISTORY_FILE = os.path.join(ROOT, "history.json")
_hist_lock = threading.Lock()
_hist_written = 0

def load_history():
    try:
        with open(HISTORY_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def record_history(snap):
    # Remember dir + group of each live session. Written at most once a minute, and
    # only when something changed, so the floor poll stays cheap.
    global _hist_written
    now = int(time.time())
    with _hist_lock:
        if now - _hist_written < 60:
            return
        h = load_history(); changed = False
        for m in snap:
            if not m.get("dir"):
                continue
            cur = h.get(m["session"]) or {}
            rec = {"dir": m["dir"], "group": m.get("group", "personal"), "seen": now,
                   "sid": m.get("sid") or cur.get("sid", "")}
            if (cur.get("dir"), cur.get("group"), cur.get("sid")) != (rec["dir"], rec["group"], rec["sid"]) \
                    or now - cur.get("seen", 0) >= 600:
                h[m["session"]] = rec; changed = True
        _hist_written = now
        if not changed:
            return
        tmp = HISTORY_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(h, f, indent=2, sort_keys=True)
        os.replace(tmp, HISTORY_FILE)

def load_tasks():
    try:
        with open(TASKS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def save_tasks(t):
    tmp = TASKS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(t, f, indent=2, sort_keys=True)
    os.replace(tmp, TASKS_FILE)

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def log_message(self, *a):
        pass  # quiet

    def _origin_ok(self):
        # No Origin header => not a browser cross-site request (curl / same-origin
        # GET). Present => must be one of our own hosts. evil.com can't forge it.
        o = self.headers.get("Origin")
        if not o:
            return True
        try:
            return urlparse(o).hostname in ALLOWED_HOSTS
        except Exception:
            return False

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self, filepath):
        # Serve an HTML file with {{OFFICE_*}} placeholders filled from config,
        # so per-instance branding (title/email) and the project→emoji map live
        # outside the committed code (.env / gitignored emoji.local.json).
        try:
            with open(filepath, encoding="utf-8") as f:
                html = f.read()
        except OSError:
            self.send_error(404); return
        for k, v in (("OFFICE_TITLE", TITLE), ("OFFICE_EMAIL", EMAIL),
                     ("OFFICE_DEV_ROOT", DEV_ROOT), ("OFFICE_EMOJI_JSON", emoji_json()),
                     ("OFFICE_STATUS_URL", STATUS_URL), ("OFFICE_FILES_URL", FILES_URL),
                     ("OFFICE_SECRETARY_NAME", SECRETARY)):
            html = html.replace("{{" + k + "}}", v)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            return self._serve_html(os.path.join(ROOT, "app.html"))
        if self.path.split("?")[0] in ("/status/", "/status/index.html"):
            return self._serve_html(os.path.join(ROOT, "status", "index.html"))
        if cfg("OFFICE_DEMO") and self.path.split("?")[0] == "/status/data.json":
            return self._demo_status()
        if self.path == "/terminal" or self.path.startswith("/terminal/"):
            if not self._origin_ok():
                return self._json({"error": "forbidden origin"}, 403)
            if cfg("OFFICE_DEMO"):
                return self._demo_terminal()
            return self._proxy_terminal()
        if self.path.split("?")[0] == "/api/dirs":
            # top-level project folders under the dev root, for the "+ New" form's suggestions
            try:
                dirs = sorted(d for d in os.listdir(DEV_ROOT)
                              if not d.startswith(".") and os.path.isdir(os.path.join(DEV_ROOT, d)))
            except OSError:
                dirs = []
            return self._json({"dirs": dirs})
        if self.path.split("?")[0] == "/api/history":
            # past sessions that are not running now, newest first: what "+ New" offers to bring back
            live = live_sessions()
            gone = [{"session": k, **v} for k, v in load_history().items() if k not in live]
            gone.sort(key=lambda r: -r.get("seen", 0))
            return self._json({"history": gone})
        if self.path.split("?")[0] == "/api/floor":
            try:
                importlib.reload(team)   # pick up team.py edits without a restart
            except Exception:
                pass
            snap = team.snapshot()
            groups = load_groups()
            pins = load_pins()
            tasks = load_tasks()
            for m in snap:
                # state files win; otherwise fall back to any value the snapshot
                # already carries (lets demo.json fixtures set group/task/pinned).
                m["group"] = groups.get(m["session"], m.get("group", "personal"))
                m["pinned"] = (m["session"] in pins) or bool(m.get("pinned"))
                m["task"] = tasks.get(m["session"], m.get("task", ""))
            if not cfg("OFFICE_DEMO"):
                try: record_history(snap)
                except Exception: pass
                try: notify_changes(snap)
                except Exception: pass
            now = int(time.time())
            if _SEC_OVERLAY and cfg("OFFICE_DEMO"):   # show/advance what the Secretary dispatched
                for m in snap:
                    ov = _SEC_OVERLAY.get(m["session"])
                    if not ov:
                        continue
                    m["ask"], m["options"], m["typed"] = "", [], None
                    if now - ov.get("ts", now) > 25:            # simulate the task completing
                        m["state"], m["verb"], m["you"] = "review", "", ov["you"]
                        m["full"] = "✓ Done — " + ov["you"]
                        m["ts"] = ov.get("ts", now) + 25
                    else:
                        m["state"], m["verb"], m["you"] = "working", ov.get("verb", "On it…"), ov["you"]
                        m["ts"] = ov["ts"]
            events = []                                          # follow-up watches that just fired
            with _WATCHES_LOCK:                                   # two concurrent polls must not double-fire/remove
                if _WATCHES:
                    by = {m["session"]: m for m in snap}
                    for w in _WATCHES[:]:
                        tm = by.get(w["session"])
                        cur = tm["state"] if tm else "gone"
                        if cur == "review" and w.get("prev") != "review":
                            act = w["act"]
                            if "dispatch" in act:
                                dm = act["dispatch"]
                                if cfg("OFFICE_DEMO"):
                                    _SEC_OVERLAY[w["session"]] = {"you": dm, "state": "working", "verb": "On it…", "ts": now}
                                    if tm:
                                        tm.update({"you": dm, "state": "working", "verb": "On it…",
                                                   "ask": "", "options": [], "typed": None, "full": ""})
                                elif w["session"] in live_sessions():
                                    try:
                                        subprocess.run(["tmux", "send-keys", "-t", w["session"], "-l", "--", dm], check=True)
                                        subprocess.run(["tmux", "send-keys", "-t", w["session"], "Enter"], check=True)
                                    except Exception:
                                        pass
                                events.append({"person": w["person"], "session": w["session"],
                                               "reply": f'{w["person"]} finished — I sent: "{dm}"'})
                            else:
                                events.append({"person": w["person"], "session": w["session"],
                                               "reply": f'{w["person"]} just finished — your move.'})
                            try:
                                _WATCHES.remove(w)
                            except ValueError:
                                pass
                        elif tm:
                            w["prev"] = cur
            return self._json({"team": snap, "groups": VALID_GROUPS, "ts": now,
                                "watch_events": events, "version": __version__})
        if self.path.split("?")[0] == "/favicon.ico":
            # Site-root favicon. The office and status pages carry their own inline
            # icons, so this only reaches tabs whose page has none: files opened
            # through the /dufs/ mount (PDFs, images, reports), which Chrome otherwise
            # shows with its blank default. Same 📁 as the header's "Files" link.
            try:
                with open(os.path.join(ROOT, "favicon.ico"), "rb") as f:
                    b = f.read()
            except OSError:
                return self._json({"error": "not found"}, 404)
            self.send_response(200)
            self.send_header("Content-Type", "image/x-icon")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(b)
            return
        # Static fallback: serve ONLY the allowlisted dashboard assets; 404 the rest
        # so config/state/source in ROOT is never exposed.
        if self.path.split("?")[0] in STATIC_OK:
            return super().do_GET()
        # /<session-name> deep-links a terminal: the app opens it on load (and puts the
        # name in the address bar when you open one), so a refresh lands in the same terminal.
        if _re.fullmatch(r"/[A-Za-z0-9_-]+", self.path.split("?")[0]):
            return self._serve_html(os.path.join(ROOT, "app.html"))
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._origin_ok():   # CSRF gate: reject cross-site POST
            return self._json({"error": "forbidden origin"}, 403)
        path = self.path.split("?")[0]
        try:
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._json({"error": "bad json"}, 400)
        if path == "/api/say":   return self._say(data)
        if path == "/api/group": return self._group(data)
        if path == "/api/pin":   return self._pin(data)
        if path == "/api/task":  return self._task(data)
        if path == "/api/secretary": return self._secretary(data)
        if path == "/api/key":   return self._key(data)
        if path == "/api/paste": return self._paste(data)
        if path == "/api/buffer":return self._buffer(data)
        if path == "/api/kill":  return self._kill(data)
        if path == "/api/rename":return self._rename(data)
        if path == "/api/new":   return self._new(data)
        return self._json({"error": "not found"}, 404)

    def _key(self, data):
        # Touch key-bar for the phone terminal: inject special keys into the tmux
        # session the terminal is attached to (send-keys). "c-x" is Ctrl-x.
        sess = str(data.get("session", ""))
        key = str(data.get("key", ""))
        if not sess or sess not in live_sessions():
            return self._json({"error": f"no live session '{sess}'"}, 409)
        NAMED = {"esc": "Escape", "tab": "Tab", "enter": "Enter", "up": "Up", "down": "Down",
                 "left": "Left", "right": "Right", "home": "Home", "end": "End"}
        try:
            if key in NAMED:
                subprocess.run(["tmux", "send-keys", "-t", sess, NAMED[key]], check=True)
            elif re.fullmatch(r"c-[a-z]", key):
                subprocess.run(["tmux", "send-keys", "-t", sess, "C-" + key[2]], check=True)
            else:
                return self._json({"error": "unknown key"}, 400)
        except Exception as e:
            return self._json({"error": str(e)}, 500)
        return self._json({"ok": True})

    def _buffer(self, data):
        # Return tmux's most-recent paste buffer (the last mouse/copy-mode
        # selection). The client writes it to the browser clipboard inside a
        # real user click — the reliable path that ttyd's async OSC 52 clipboard
        # write (deprecated execCommand, fires outside the gesture) cannot take.
        try:
            out = subprocess.run(["tmux", "show-buffer"],
                                 capture_output=True, text=True, timeout=3).stdout
        except Exception:
            out = ""
        return self._json({"text": out})

    def _paste(self, data):
        # Paste the browser clipboard into the attached tmux pane via a tmux buffer +
        # bracketed paste, so multi-line text is inserted as one paste (not run line by line).
        sess = str(data.get("session", ""))
        text = str(data.get("text", ""))
        if not sess or sess not in live_sessions():
            return self._json({"error": f"no live session '{sess}'"}, 409)
        if text:
            try:
                subprocess.run(["tmux", "set-buffer", "--", text], check=False)
                subprocess.run(["tmux", "paste-buffer", "-p", "-t", sess], check=False)
            except Exception as e:
                return self._json({"error": str(e)}, 500)
        return self._json({"ok": True})

    def _pin(self, data):
        sess = str(data.get("session", ""))
        if not sess or sess not in live_sessions():
            return self._json({"error": f"no live session '{sess}'"}, 409)
        pins = load_pins()
        if data.get("pinned"):
            pins.add(sess)
        else:
            pins.discard(sess)
        save_pins(pins)
        return self._json({"ok": True, "session": sess, "pinned": sess in pins})

    def _task(self, data):
        # Set/clear a session's focus note. Blank text removes the note (delete key).
        sess = str(data.get("session", ""))
        text = str(data.get("text", "")).strip()
        if not sess or sess not in live_sessions():
            return self._json({"error": f"no live session '{sess}'"}, 409)
        t = load_tasks()
        if text:
            t[sess] = text
        else:
            t.pop(sess, None)
        save_tasks(t)
        return self._json({"ok": True})

    def _secretary(self, data):
        # Explicit actions are handled deterministically (instant, safe); everything else
        # goes to Claude, which reasons over the WHOLE floor instead of keyword-matching.
        #   watch     "ping me when Lena's done" / "when Mara finishes, have her deploy"
        #   broadcast "tell everyone on payments to pull main"
        #   dispatch  an EXPLICIT imperative to one person: "ask/tell Lena to X" or "Lena: X"
        #   ask       everything else (DEFAULT) -> _sec_ask_claude reasons and answers.
        # "/btw ..." (or "btw ...") forces the reasoning path even if it looks like a command.
        text = str(data.get("text", "")).strip()
        if not text:
            return self._json({"error": "Tell me what you need."}, 400)
        mb = re.match(r'^\s*/?btw\b[\s,:]*(.+)$', text, re.I)
        force_ask = bool(mb)
        if mb:
            text = mb.group(1).strip()
        try:
            snap = team.snapshot()
        except Exception:
            snap = []
        if not snap:
            return self._json({"error": "The floor is empty right now."}, 409)
        _g = load_groups()
        for _m in snap:
            _m["group"] = _g.get(_m["session"], _m.get("group", "personal"))
        if not force_ask:
            if self._sec_watch(text, snap):
                return
            if self._sec_broadcast(text, snap):
                return
            if self._sec_dispatch(text, snap):
                return
        return self._sec_ask_claude(text, snap)

    def _sec_dispatch(self, text, snap):
        # A dispatch is an EXPLICIT imperative aimed at one person. Merely mentioning a
        # name ("is alice active?") is NOT a dispatch; it falls through to answer, so
        # we never type a stray sentence into a working session. Returns True or None.
        low = text.lower().strip()
        people = {}   # first-name(lower) -> (Person, session)
        for m in snap:
            first = (m.get("person") or "").strip().split(" ")[0]
            if first:
                people.setdefault(first.lower(), (m.get("person") or first, m["session"]))
        if not people:
            return None
        names = "|".join(re.escape(n) for n in sorted(people, key=len, reverse=True))
        # "[please/hey/<secretary>] ask|tell|have|get|remind|nudge|dm|message|ping|send NAME [to] INSTR"
        sec = re.escape((SECRETARY or "").strip().split(" ")[0].lower())   # let people address her by her configured name
        fillers = "please|hey|can you|could you|pls" + (("|" + sec) if sec else "")
        m = re.match(r'^(?:(?:' + fillers + r')[\s,:]+)?'
                     r'(?:ask|tell|have|get|remind|nudge|dm|message|ping|send)\s+'
                     r'(' + names + r')\b[\s,:]*(?:to\s+)?(.+)$', low)
        if not m:                                  # or the terse "NAME: INSTR" / "NAME please INSTR"
            m = (re.match(r'^(' + names + r')\s*:\s*(.+)$', low)
                 or re.match(r'^(' + names + r')\s+please\s+(.+)$', low))
        if not m:
            return None
        person, session = people[m.group(1).lower()]
        instr = m.group(2).strip(" ,.")
        # If what follows the name is really a question about them, answer instead of sending.
        if (not instr or instr.startswith("'s ")
                or re.match(r"^(what|whats|why|who|whom|whose|how|which|is|are|status|state|doing|up to|there|around|online|active|busy|free|ok|okay|available|still|working|done|stuck)\b", instr)):
            return None
        msg = (instr[0].upper() + instr[1:]) if re.match(r'(?i)^please\b', instr) \
              else "Please " + instr[0].lower() + instr[1:]
        if cfg("OFFICE_DEMO"):
            _SEC_OVERLAY[session] = {"you": msg, "state": "working", "verb": "On it…", "ts": int(time.time())}
        else:
            if session not in live_sessions():
                self._json({"error": f"{person} isn't a live session right now."}, 409); return True
            try:
                subprocess.run(["tmux", "send-keys", "-t", session, "-l", "--", msg], check=True)
                subprocess.run(["tmux", "send-keys", "-t", session, "Enter"], check=True)
            except Exception:
                self._json({"error": f"Couldn't reach {person}."}, 502); return True
        self._json({"ok": True, "kind": "dispatch", "person": person, "session": session,
                    "message": msg, "reply": f'Passed to {person} — "{msg}"'})
        return True

    def _sec_ask_claude(self, text, snap):
        # The real-AI path: hand Claude the whole floor + the operator's message and let it
        # reason, instead of keyword-matching. Falls back to the deterministic _sec_answer
        # if the model call fails or times out, so the secretary always replies with something.
        floor = [{
            "name": m.get("person") or m.get("project"),
            "project": m.get("project"),
            "state": m.get("state"),
            "attached": m.get("attached"),
            "needs_you": (m.get("ask") or "")[:220],
            "your_last_instruction": (m.get("you") or "")[:180],
            "its_last_message": (m.get("full") or "")[:280],
        } for m in snap]
        prompt = (
            f"You are {SECRETARY}, chief of staff for a one-person software studio. The 'office' "
            f"is a wall of AI coding agents, each in its own terminal; you report to the operator "
            f"ABOUT their agents. States: working = coding right now; waiting = it needs the "
            f"operator (asked a question or has an unsent draft — see needs_you); review = "
            f"finished, operator's move.\n\n"
            f"Answer the operator's message in 1-3 short, warm, plain sentences, grounded ONLY in "
            f"the floor below. Reason about it: e.g. if they ask why someone looks active, look at "
            f"that agent's state and its_last_message and explain. If they want an instruction "
            f"relayed to an agent, do NOT do it yourself — tell them to type \"ask <name> to "
            f"<task>\". No preamble; light **bold** for emphasis is fine, but no bullet lists "
            f"unless asked, and never use em-dashes. Write the way a sharp person talks.\n\n"
            f"FLOOR (JSON): {json.dumps(floor, ensure_ascii=False)}\n\n"
            f"OPERATOR: {text}"
        )
        try:
            # NO TOOLS. The secretary only reasons over the prompt text below. The floor can carry
            # untrusted session content, so a prompt injection must never reach Bash/Write/etc.
            # --disallowed-tools blocks them structurally (verified: holds even when permission
            # checks are bypassed). Put --model AFTER the list so the variadic flag doesn't
            # swallow the prompt positional.
            # Run from a scratch dir, never from the office checkout: Claude files each
            # run's transcript under the cwd's project folder, and a session working in
            # the office repo would then be shown the secretary's answer as its own.
            os.makedirs(SEC_CWD, exist_ok=True)
            out = subprocess.run([CLAUDE_BIN, "-p", "--strict-mcp-config",   # --strict-mcp-config: no MCP tools load either
                                  "--disallowed-tools", "Bash", "Edit", "Write", "NotebookEdit",
                                  "Read", "Glob", "Grep", "WebFetch", "WebSearch", "Task",
                                  "SlashCommand", "TodoWrite",
                                  "--model", SEC_MODEL, prompt],
                                 capture_output=True, text=True, timeout=45, cwd=SEC_CWD)
            reply = (out.stdout or "").strip()
        except Exception:
            reply = ""
        if not reply:
            return self._sec_answer(text, snap)   # model unavailable -> deterministic fallback
        reply = re.sub(r'\s*—\s*', ', ', reply).replace('–', '-')   # strip the AI em-dash tell for good
        reply = re.sub(r',\s*,', ',', reply)
        low = reply.lower()          # flash the cards of any agents she named
        sess = [m["session"] for m in snap
                if (m.get("person") or "").split(" ")[0]
                and re.search(r'\b' + re.escape((m.get("person") or "").split(" ")[0].lower()) + r'\b', low)][:6]
        return self._json({"ok": True, "kind": "answer", "reply": reply, "sessions": sess})

    def _sec_answer(self, text, snap):
        # Deterministic fallback. Answer questions about the floor: "who's on payments",
        # "who's waiting on me", "how's everyone".
        low = text.lower()
        STATE = {"working": "working", "waiting": "waiting on you", "review": "done, your move"}
        def who(m): return f'{m.get("person") or m.get("project")} on {m.get("project")}'
        def ans(reply, sess=None):
            return self._json({"ok": True, "kind": "answer", "reply": reply, "sessions": sess or []})
        # If the question names people on the floor, answer about THEM before the generic
        # state lists, so "is alice working?" reports Alice, not the global working list.
        named = [m for m in snap
                 if (m.get("person") or "").strip().split(" ")[0]
                 and re.search(r'\b' + re.escape((m.get("person") or "").strip().split(" ")[0].lower()) + r'\b', low)]
        if named:
            if len(named) == 1:
                m = named[0]
                return ans(f'{m.get("person") or m.get("project")} — on {m.get("project")} ({STATE.get(m["state"], m["state"])}).',
                           [m["session"]])
            return ans(", ".join(f'{m.get("person")} ({STATE.get(m["state"], m["state"])})' for m in named[:10]),
                       [m["session"] for m in named])
        if re.search(r'\b(waiting|need(?:s)? me|for me|my turn|blocked|stuck)\b', low):
            w = [m for m in snap if m["state"] == "waiting"]
            return ans("Nobody's waiting on you right now. 🎉" if not w
                       else f"{len(w)} waiting on you: " + ", ".join(who(m) for m in w[:10]),
                       [m["session"] for m in w])
        if re.search(r'\b(finished|done|review|my move|ready|to review)\b', low):
            r = [m for m in snap if m["state"] == "review"]
            return ans("Nothing's parked for review." if not r
                       else f"{len(r)} finished, your move: " + ", ".join(who(m) for m in r[:10]),
                       [m["session"] for m in r])
        if re.search(r'\b(everyone|whole team|the team|overview|summ|who.?s here)\b', low):
            w = sum(1 for m in snap if m["state"] == "working")
            q = sum(1 for m in snap if m["state"] == "waiting")
            rv = sum(1 for m in snap if m["state"] == "review")
            return ans(f"{len(snap)} on the floor — {w} working, {q} waiting on you, {rv} finished.")
        if re.search(r'\bworking\b', low) and "working on" not in low and "work on" not in low:
            w = [m for m in snap if m["state"] == "working"]
            return ans("No one's actively churning right now." if not w
                       else f"{len(w)} working: " + ", ".join(who(m) for m in w[:10]),
                       [m["session"] for m in w])
        # topic / person keyword
        m = re.search(r'(?:working on|work on|on the|handling|\bowns?\b|\bhas\b|\babout\b|\bfor\b|\bon\b)\s+(.+)$', low)
        if m:
            topic = m.group(1)
        else:
            m2 = (re.search(r'(?:doing|status of|up to|how.?s|what.?s|about)\s+([a-z][\w-]+)', low)
                  or re.search(r'\b([a-z][\w-]+)\s+(?:doing|status|up to)\b', low))
            topic = m2.group(1) if m2 else re.sub(
                r'^(?:who|what|which|whose|where|is|are|does|do|working|on|the|a|an|list|show|status|of|me|our|my|us)\b\s*',
                '', low).strip(" ?.")
        _STOP = {"the", "app", "apps", "site", "website", "project", "thing", "stuff", "doing",
                 "work", "task", "one", "who", "me", "us", "my", "on", "is", "of", "to", "in",
                 "it", "at", "or", "an", "he", "we", "do", "so", "up", "by", "for", "and", "are"}
        words = [w for w in re.findall(r'[a-z0-9]+', topic or "") if len(w) >= 2 and w not in _STOP]
        if not words:
            return ans("Ask me things like “who's on payments”, “what's Lena doing”, or “who's waiting on me”.")
        def hit(m):
            hay = (str(m.get("session", "")) + " " + str(m.get("project", "")) + " " + str(m.get("person", ""))).lower()
            return any(re.search(r'\b' + re.escape(w) + r'\b', hay) for w in words)   # \b so a short name isn't matched inside a longer word
        hits = [m for m in snap if hit(m)]
        label = (topic or "").strip()
        if not hits:
            return ans(f"No one's on “{label}” right now.")
        if len(hits) == 1:
            m = hits[0]
            return ans(f'{m.get("person")} — on {m.get("project")} ({STATE.get(m["state"], m["state"])}).', [m["session"]])
        return ans(f"{len(hits)} on “{label}”: " + ", ".join(f'{m.get("person")} on {m.get("project")}' for m in hits[:10]),
                   [m["session"] for m in hits])

    def _sec_broadcast(self, text, snap):
        # "tell everyone on payments to pull main" / "ask all clients to commit". Returns
        # True (handled) or None (not a broadcast — let single-dispatch try).
        low = text.lower()
        if not re.search(r'\b(everyone|everybody|all|the (?:whole )?team|all of them)\b', low):
            return None
        m = re.search(r'\b(?:everyone|everybody|all|the (?:whole )?team|all of them)\b(.*?)\bto\b\s+(.+)$', low)
        if m:
            scope, instr = m.group(1), m.group(2)
        else:
            m2 = re.search(r'\b(?:everyone|everybody|all|the (?:whole )?team|all of them)\b\s+(.+)$', low)
            if not m2:
                return None
            scope, instr = "", m2.group(1)
        instr = re.sub(r'^(?:please|to)\s+', '', instr.strip(), flags=re.I).strip(" ,.")
        if not instr:
            self._json({"error": "Broadcast what, exactly?"}, 400); return True
        _STOP2 = {"on", "in", "the", "of", "to", "who", "is", "are", "a", "an", "and", "my", "our"}
        sw = [w for w in re.findall(r'[a-z0-9]+', scope) if len(w) >= 2 and w not in _STOP2]
        grp = next((w for w in sw if w.rstrip("s") in ("client", "venture", "personal")), None)
        if grp:
            gmap = {"client": "clients", "venture": "ventures", "personal": "personal"}
            targets = [x for x in snap if x.get("group") == gmap[grp.rstrip("s")]]
        elif sw:
            targets = [x for x in snap if any(
                re.search(r'\b' + re.escape(w) + r'\b', (str(x.get("session", "")) + " " + str(x.get("project", "")) + " " + str(x.get("person", ""))).lower()) for w in sw)]
        else:
            targets = list(snap)
        if not targets:
            self._json({"error": "No one matches that group."}, 409); return True
        msg = "Please " + (instr[0].lower() + instr[1:])
        sent, now = [], int(time.time())
        for x in targets:
            s = x["session"]
            if cfg("OFFICE_DEMO"):
                _SEC_OVERLAY[s] = {"you": msg, "state": "working", "verb": "On it…", "ts": now}
                sent.append(x)
            elif s in live_sessions():
                try:
                    subprocess.run(["tmux", "send-keys", "-t", s, "-l", "--", msg], check=True)
                    subprocess.run(["tmux", "send-keys", "-t", s, "Enter"], check=True)
                    sent.append(x)
                except Exception:
                    pass
        names = ", ".join(x.get("person") or x.get("project") for x in sent[:12])
        self._json({"ok": True, "kind": "broadcast", "count": len(sent),
                    "sessions": [x["session"] for x in sent], "message": msg,
                    "reply": f'Passed to {len(sent)} — {names}: "{msg}"'})
        return True

    def _sec_watch(self, text, snap):
        # "ping me when Mara's done" (notify) or "when Lena finishes, have her deploy"
        # (queued dispatch). Returns True (handled) or None (not a watch).
        low = text.lower()
        mw = re.search(r'\b(?:when|once|after)\s+([a-z][\w-]*?)(?:\'s|s)?\s+(?:is\s+|are\s+)?'
                       r'(?:done|finished|finish(?:es)?|free|ready|complete|wraps? up|is up)\b', low)
        if not mw:
            return None
        name = mw.group(1)
        person = session = start = None
        for m in snap:
            if (m.get("person") or "").split(" ")[0].lower() == name:
                person, session, start = m.get("person") or name.title(), m["session"], m["state"]
                break
        if not session:
            self._json({"error": f"I don't see anyone called {name.title()}."}, 400); return True
        rest = (low[:mw.start()] + " " + low[mw.end():]).strip(" ,.")
        act = {"notify": True}
        md = (re.search(r'\b(?:have|tell|ask|get)\s+(?:her|him|them|' + re.escape(name) + r')\s+(?:to\s+)?(.+)$', rest)
              or re.search(r'\bthen\s+(.+)$', rest))
        if md:
            instr = md.group(1).strip(" ,.")
            if instr and not re.match(r'(?i)^(ping|notify|tell|let|remind)\s+me\b', instr):
                act = {"dispatch": "Please " + (instr[0].lower() + instr[1:])}
        with _WATCHES_LOCK:
            _WATCHES.append({"session": session, "person": person, "act": act, "prev": start, "ts": int(time.time())})
        if "dispatch" in act:
            self._json({"ok": True, "kind": "watch",
                        "reply": f'Got it — when {person} finishes, I\'ll send: "{act["dispatch"]}"'})
        else:
            self._json({"ok": True, "kind": "watch", "reply": f"Got it — I'll ping you when {person} finishes."})
        return True

    def _demo_status(self):
        # Demo mode: generate fictional usage data (status/demo_status.py) so the
        # dashboard renders without touching real ~/.claude transcripts.
        try:
            sp = os.path.join(ROOT, "status")
            if sp not in sys.path:
                sys.path.insert(0, sp)
            import demo_status as _ds
            importlib.reload(_ds)
            body = json.dumps(_ds.build()).encode("utf-8")
        except Exception as e:
            body = json.dumps({"error": str(e)}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _demo_terminal(self):
        # Demo mode: render a mock Claude-Code terminal for the requested card
        # (from demo.json), so clicking a terminal shows something without real tmux/ttyd.
        import html as _h
        from urllib.parse import urlparse as _up, parse_qs
        sess = (parse_qs(_up(self.path).query).get("arg") or [""])[0]
        ent = {}
        try:
            with open(os.path.join(ROOT, "demo.json"), encoding="utf-8") as f:
                for e in json.load(f):
                    if e.get("session") == sess:
                        ent = e; break
        except Exception:
            pass
        person = _h.escape(str(ent.get("person", "demo")))
        project = _h.escape(str(ent.get("project", sess or "demo")))
        you, full = ent.get("you", ""), ent.get("full", "")
        verb, working = ent.get("verb", ""), ent.get("state") == "working"
        rows = [f'<div class="dim">✻ Claude Code — demo session · {person}/{project}</div>', '<div class="sp"></div>']
        if you:
            rows += [f'<div class="you">&gt; {_h.escape(str(you))}</div>', '<div class="sp"></div>']
        if full:
            rows += [f'<div class="asst">⏺ {_h.escape(str(full))}</div>', '<div class="sp"></div>']
        if working and verb:
            rows.append(f'<div class="work">✻ {_h.escape(str(verb))} <span class="dim">(esc to interrupt)</span></div>')
        page = ("<!doctype html><html><head><meta charset=utf-8><title>" + project + "</title><style>"
                "html,body{margin:0;height:100%;background:#0c0f14;color:#e8eef6;"
                "font:14px/1.65 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}"
                ".wrap{padding:18px 20px 96px;max-width:920px}.dim{color:#7c8797}"
                ".you{color:#8fd4ff;white-space:pre-wrap}.asst{white-space:pre-wrap}"
                ".work{color:#3fb6ff}.sp{height:10px}"
                ".box{position:fixed;left:20px;right:20px;bottom:34px;border:1px solid #252d3a;"
                "border-radius:8px;padding:9px 12px;color:#7c8797;background:#0c0f14}"
                ".cur{display:inline-block;width:8px;height:15px;background:#3fb6ff;vertical-align:-2px;"
                "animation:b 1.1s steps(1) infinite}@keyframes b{50%{opacity:0}}"
                ".bar{position:fixed;left:0;right:0;bottom:0;padding:6px 20px;background:#141922;"
                "border-top:1px solid #252d3a;color:#7c8797;font-size:12px}"
                "</style></head><body><div class=\"wrap\">" + "\n".join(rows) + "</div>"
                "<div class=\"box\">&gt; <span class=\"cur\"></span></div>"
                "<div class=\"bar\">demo terminal · " + person + "/" + project + " · Opus 4.8 · not a real shell</div>"
                "</body></html>")
        b = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _proxy_terminal(self):
        # Reverse-proxy /terminal/* to the localhost-only ttyd (base-path /terminal).
        # Works for the xterm page/assets AND the websocket upgrade: after forwarding
        # the request we just pipe raw bytes both ways until close.
        is_ws = self.headers.get("Upgrade", "").lower() == "websocket"
        up = None
        try:
            up = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            up.settimeout(10)
            up.connect(TTYD_SOCK)
            up.settimeout(None)
        except Exception:
            if up is not None:
                try: up.close()               # don't leak the fd when ttyd is down
                except Exception: pass
            return self._json({"error": "terminal server unavailable"}, 502)
        lines = [f"{self.command} {self.path} HTTP/1.1"]
        for k, v in self.headers.items():
            if k.lower() == "connection" and not is_ws:
                continue
            lines.append(f"{k}: {v}")
        if not is_ws:
            lines.append("Connection: close")   # force EOF so the pipe finishes
        up.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        clen = int(self.headers.get("Content-Length", 0) or 0)
        if clen:
            up.sendall(self.rfile.read(clen))
        self.close_connection = True
        cli = self.connection

        def pipe(src, dst):
            try:
                while True:
                    b = src.recv(65536)
                    if not b:
                        break
                    dst.sendall(b)
            except Exception:
                pass
            finally:
                try: dst.shutdown(socket.SHUT_WR)
                except Exception: pass

        t = threading.Thread(target=pipe, args=(up, cli), daemon=True)
        t.start()
        pipe(cli, up)
        try: up.close()
        except Exception: pass

    def _say(self, data):
        sess = str(data.get("session", ""))
        text = str(data.get("text", ""))
        submit = bool(data.get("submit"))   # submit the existing draft (Enter only)
        if not sess or (not text and not submit):
            return self._json({"error": "session + (text or submit) required"}, 400)
        if sess not in live_sessions():
            return self._json({"error": f"no live session '{sess}'"}, 409)
        try:
            if text:
                # typing replaces any dim autosuggestion, then submit. "--" so text
                # starting with "-" is typed literally, not parsed as a tmux flag.
                subprocess.run(["tmux", "send-keys", "-t", sess, "-l", "--", text], check=True)
            else:
                # submit=true means "send the suggestion sitting in the box". That text is
                # a dim autosuggestion (ghost), NOT real input — Enter alone submits empty.
                # Right accepts the whole suggestion into the input first (like shell autosuggest).
                subprocess.run(["tmux", "send-keys", "-t", sess, "Right"], check=True)
            subprocess.run(["tmux", "send-keys", "-t", sess, "Enter"], check=True)
        except (subprocess.CalledProcessError, OSError):
            return self._json({"error": f"couldn't reach '{sess}'"}, 502)
        return self._json({"ok": True, "session": sess})

    def _group(self, data):
        sess = str(data.get("session", ""))
        grp = str(data.get("group", ""))
        if grp not in VALID_GROUPS:
            return self._json({"error": f"group must be one of {VALID_GROUPS}"}, 400)
        if not sess or sess not in live_sessions():
            return self._json({"error": f"no live session '{sess}'"}, 409)
        g = load_groups()
        g[sess] = grp
        save_groups(g)
        return self._json({"ok": True, "session": sess, "group": grp})

    def _rename(self, data):
        # Rename a session in tmux and carry its Office state (category, pin, focus note,
        # history) over to the new name, so the card keeps everything it had.
        old = str(data.get("session", ""))
        new = safe_session(data.get("name", ""))
        if not old or old not in live_sessions():
            return self._json({"error": f"no live session '{old}'"}, 409)
        if not new:
            return self._json({"error": "name required (letters/numbers/-/_)"}, 400)
        if new == old:
            return self._json({"ok": True, "session": new})
        if new in live_sessions():
            return self._json({"error": f"session '{new}' already exists"}, 409)
        try:
            subprocess.run(["tmux", "rename-session", "-t", old, new], check=True)
        except (subprocess.CalledProcessError, OSError):
            return self._json({"error": f"couldn't rename '{old}'"}, 502)
        g = load_groups()
        if old in g:
            g[new] = g.pop(old); save_groups(g)
        p = load_pins()
        if old in p:
            p.discard(old); p.add(new); save_pins(p)
        t = load_tasks()
        if old in t:
            t[new] = t.pop(old); save_tasks(t)
        with _hist_lock:
            h = load_history()
            if old in h:
                h[new] = h.pop(old)
                tmp = HISTORY_FILE + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(h, f, indent=2, sort_keys=True)
                os.replace(tmp, HISTORY_FILE)
        return self._json({"ok": True, "session": new})

    def _kill(self, data):
        sess = str(data.get("session", ""))
        if sess not in live_sessions():
            return self._json({"error": f"no live session '{sess}'"}, 409)
        try:
            subprocess.run(["tmux", "kill-session", "-t", sess], check=True)
        except (subprocess.CalledProcessError, OSError):
            return self._json({"error": f"couldn't kill '{sess}'"}, 502)
        g = load_groups()
        if g.pop(sess, None) is not None:
            save_groups(g)
        return self._json({"ok": True, "killed": sess})

    def _new(self, data):
        name = safe_session(data.get("name", ""))
        d = str(data.get("dir", "")).strip() or DEV_ROOT
        grp = str(data.get("group", "personal"))
        if not name:
            return self._json({"error": "name required (letters/numbers/-/_)"}, 400)
        if name in live_sessions():
            return self._json({"error": f"session '{name}' already exists"}, 409)
        # Confine new folders to ~/develop. realpath resolves ../ and symlinks so a
        # crafted dir can't escape the root; then create it if it doesn't exist.
        d = os.path.realpath(d)
        if d != DEV_ROOT and not d.startswith(DEV_ROOT + os.sep):
            return self._json({"error": f"directory must be under {DEV_ROOT}"}, 400)
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            return self._json({"error": "could not create that directory"}, 400)
        launch = CLAUDE_BIN if os.path.exists(CLAUDE_BIN) else "claude"
        resume = str(data.get("resume", ""))
        if resume and re.fullmatch(r"[0-9a-f-]{36}", resume):
            launch += " --resume " + resume          # bring back: pick the conversation up where it was
        # When Claude exits (Ctrl-D twice, /exit, a crash) drop to a shell instead of
        # letting tmux close the session — the card stays and `claude --continue` is a
        # keystroke away. tmux runs the string through the user's default shell.
        launch += "; exec " + shlex.quote(_login_shell())
        try:
            subprocess.run(["tmux", "new-session", "-d", "-s", name, "-c", d, launch], check=True)
        except (subprocess.CalledProcessError, OSError):
            return self._json({"error": "could not start the session"}, 502)
        if grp in VALID_GROUPS:
            g = load_groups(); g[name] = grp; save_groups(g)
        return self._json({"ok": True, "created": name, "dir": d})

def main():
    ip = tailscale_ip()
    if not ip:
        sys.stderr.write("office/server: no tailscale IP; refusing to bind (never expose the reply endpoint off-tailnet)\n")
        sys.exit(1)
    ALLOWED_HOSTS.add(ip)   # the tailnet IP is a valid origin for our own app
    name = tailscale_dnsname()
    if name:
        ALLOWED_HOSTS.add(name)   # live tailnet DNS name (rename-proof)
    os.chdir(ROOT)
    httpd = ThreadingHTTPServer((ip, PORT), Handler)
    sys.stderr.write(f"office/server: http://{ip}:{PORT}/  ({name or 'office'}:{PORT})\n")
    httpd.serve_forever()

if __name__ == "__main__":
    main()
