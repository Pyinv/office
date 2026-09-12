"""Shared team-state reader for the office floor + web server.

Source of truth for WHAT CLAUDE SAID is the transcript JSONL (the real last
assistant message), not the rendered pane — scraping the screen grabs the
statusline widget ("branch | +N -N | 8h ago"), not Claude's question.

Pane scraping is used only for two things the transcript can't tell us:
  - whether you've typed text into the prompt but not sent it
  - the "ghost" sessions whose transcript was deleted by retention

Person + project come from the tmux session name (`lena-payments` -> Lena / payments).
tmux is the single source of truth for identity; no roster file.
"""
import subprocess, re, os, json, glob, time, socket

HOME = os.path.expanduser("~")
PROJ = os.path.join(HOME, ".claude", "projects")
# ~/.claude/projects encodes each cwd by replacing "/" with "-"; this is the
# key for the bare home dir, which holds no real session and is skipped.
HOME_KEY = HOME.replace("/", "-")
HOST = re.escape(socket.gethostname().split(".")[0])   # for the "hostname:" statusline/prompt prefix
GHOST_AFTER = 3600   # a no-transcript session older than this is a ghost; newer = just-created

def _tmux(*a):
    try:
        return subprocess.run(["tmux", *a], capture_output=True, text=True).stdout
    except (FileNotFoundError, OSError):
        return ""      # no tmux (or it errored) -> empty; the floor just shows nothing

def sessions():
    out = _tmux("list-panes", "-a", "-F",
                "#{session_name}\t#{session_attached}\t#{pane_current_path}\t#{session_activity}\t#{session_created}")
    seen = {}
    for line in out.strip().splitlines():
        p = line.split("\t")
        if len(p) < 3:
            continue
        name, att, path = p[0], p[1], p[2]
        act = int(p[3]) if len(p) > 3 and p[3].isdigit() else 0   # tmux last-activity epoch
        crt = int(p[4]) if len(p) > 4 and p[4].isdigit() else 0   # tmux session-created epoch
        seen.setdefault(name, (att != "0", path, act, crt))
    return seen

def split_name(s):
    if "-" in s:
        person, project = s.split("-", 1)
        return person.capitalize(), project
    return "", s

# ── transcript side ───────────────────────────────────────────────────────
_TCACHE = {}  # path -> (mtime, parsed)

def _tail(path, kb=256):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(max(0, size - kb * 1024))
        return f.read().decode("utf-8", "ignore").splitlines()

def _text_of(msg):
    if not isinstance(msg, dict):
        return ""
    c = msg.get("content", [])
    if isinstance(c, str):
        return c
    if not isinstance(c, list):
        return ""
    return " ".join(b.get("text", "") for b in c
                    if isinstance(b, dict) and b.get("type") == "text")

# user "messages" in a transcript include skill/command/tool injections, not just
# what you typed. Keep only text that reads like a real prompt.
_INJECT = ("Base directory for this skill", "Caveat:", "[Request interrupted",
           "<command", "<system-reminder", "<local-command", "<user-memory",
           "This session is being continued", "<!-- AUTO-GENERATED", "Please continue")
def _is_real_prompt(t):
    if not t or t.startswith("<"):
        return False
    return not any(b in t[:220] for b in _INJECT)

def _parse_one(f):
    """Parse one transcript's tail into a record (cached by mtime)."""
    try:
        mt = os.path.getmtime(f)
    except OSError:
        return None                      # a .jsonl vanished mid-scan (retention race)
    hit = _TCACHE.get(f)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        tail = _tail(f)
    except OSError:
        return None
    cwd = last_asst = last_role = last_ts = asst_ts = None
    pending_user = you = None            # you = the prompt the last answer responds to
    for line in tail:
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("cwd"):
            cwd = o["cwd"]
        t = o.get("type")
        if t == "assistant":
            txt = _text_of(o.get("message", {}))
            last_role = "assistant"
            last_ts = o.get("timestamp") or last_ts
            asst_ts = o.get("timestamp") or asst_ts   # last time Claude replied
            if txt.strip():
                last_asst = txt.strip()
                you = pending_user        # pair this answer with the prompt before it
        elif t == "user":
            txt = _text_of(o.get("message", {})).strip()
            if _is_real_prompt(txt):
                last_role = "user"
                pending_user = txt
            elif not txt:
                last_role = "tool_result"
            last_ts = o.get("timestamp") or last_ts
    res = {"cwd": cwd, "asst": last_asst, "role": last_role, "ts": last_ts,
           "asst_ts": asst_ts, "you": you}
    _TCACHE[f] = (mt, res)
    return res

def _parse_latest(dirpath):
    files = glob.glob(dirpath + "/*.jsonl")
    if not files:
        return None
    # Pick the transcript by its last real MESSAGE time, not the file's mtime. A
    # resumed or idle conversation can have its file re-touched (title/agent-name/
    # resume metadata) with no new message, bumping its mtime above the chat you're
    # actually working in — so "newest mtime" can point at a days-old transcript.
    # Rank the few most-recently-touched files, then keep whichever has the newest
    # actual message.
    try:
        files.sort(key=os.path.getmtime, reverse=True)
    except OSError:
        return None
    best = None
    best_ts = -1
    for f in files[:8]:
        r = _parse_one(f)
        if not r:
            continue
        rt = _epoch(r.get("ts"))
        if rt > best_ts:
            best, best_ts = r, rt
    return best

def _transcript_index():
    idx = {}
    for d in (os.listdir(PROJ) if os.path.isdir(PROJ) else []):
        dp = os.path.join(PROJ, d)
        if not os.path.isdir(dp) or d == HOME_KEY:
            continue
        r = _parse_latest(dp)
        if r and r["cwd"] and r["cwd"] != HOME:
            idx[r["cwd"]] = r
    return idx

def _by_dir(path):
    # Claude stores a session's transcript under ~/.claude/projects/<cwd with "/" -> "-">,
    # keyed by where it STARTED. The tmux pane cwd is usually that start dir, so match it
    # directly. Catches sessions that cd'd away mid-run — their recorded message cwd (and so
    # the index) then points at a different project, which would otherwise read as a ghost.
    if not path:
        return None
    key = path.replace("/", "-")
    dp = os.path.join(PROJ, key)
    if key != HOME_KEY and os.path.isdir(dp):
        return _parse_latest(dp)
    return None

def _join(path, idx, project=None):
    """Match a session's cwd to its transcript. Exact wins. Else the closest ANCESTOR
    transcript (the session sits inside a dir Claude was launched in). Else a DESCENDANT
    transcript (Claude cd'd into a subdir) — but a shallow session (a monorepo root) can
    sit above several subprojects' transcripts, so if more than one descendant matches,
    disambiguate by the session's project name; if still ambiguous return None and read
    the live pane instead of showing another session's work.
    $HOME excluded (it prefixes everything and would cross-attribute)."""
    if not path or path == HOME:
        return None
    if path in idx:
        return idx[path]
    ancestors = [c for c in idx if c != HOME and path.startswith(c + "/")]
    if ancestors:
        return idx[max(ancestors, key=len)]     # closest dir Claude was launched in
    descendants = [c for c in idx if c != HOME and c.startswith(path + "/")]
    if len(descendants) == 1:
        return idx[descendants[0]]
    if len(descendants) > 1 and project:         # ambiguous root: pick the child matching the project
        p = "/" + project.lower() + "/"
        named = [c for c in descendants if p in (c + "/").lower()]
        if len(named) == 1:
            return idx[named[0]]
    return None                                  # can't tell which child -> fall back to the pane

# TUI noise that is not part of what Claude actually said: spinner timers, token
# hints, the feedback survey, interrupt hints. Filtered from asks and ghost blocks.
_NOISE = re.compile(
    r'^\s*[✻✳✽⠂⠈⠐⠠⢀⡀⣾⣽⣻⢿⡿⣟⣯⣷…]'
    r'|(Cogitat|Churn|Crunch|Cerebrat|Cook|Work|Befuddl|Ponder|Think|Brew|Simmer|Concoct|Percolat|Quantumiz|Discombobulat)\w*\s+for\s+\d'
    r'|esc to interrupt|new task\?|/clear to save|↓\s*\d.*tokens'
    r'|How is Claude doing this session|^\s*\d:\s*(Bad|Fine|Good)\b|Dismiss')

# Only offer a canned button when the question is genuinely polar. Numbered "1./2."
# buttons are deliberately NOT derived — step lists ("1. Open Telegram") look identical
# to choices and would mislead. Everything non-polar just gets the free-text reply box.
_POLAR = re.compile(r'\b(want me to|shall i|should i|do you want|want to|would you like|'
                    r'ok to|shall we|can i|may i|do you|are you|is it|does that)\b', re.I)
def _options(ask, is_q):
    if is_q and _POLAR.search(ask or ""):
        return ["yes", "no"]
    return []

def _extract_ask(text):
    """The actual question line: prefer the last line whose '?' is real punctuation
    (not a URL query string), skipping TUI noise."""
    lines = [re.sub(r'\s+', ' ', l.strip()) for l in text.splitlines() if l.strip()]
    lines = [l for l in lines if not _NOISE.search(l)]
    if not lines:
        return "", False
    for l in reversed(lines):
        if "?" in re.sub(r'https?://\S+', '', l):   # ignore '?' inside URLs (e.g. ...?a=b)
            return l[:240], True
    return lines[-1][:240], False

# ── pane side (prompt state only) ─────────────────────────────────────────
# "Actively working RIGHT NOW". The reliable signal across Claude Code versions is
# the LIVE elapsed timer "(22s · …" the spinner shows while generating (older builds
# said "esc to interrupt"). A completed timer reads "Boogied for 42s" (no parens, no
# ellipsis), so it won't match — that avoids the old false-positive.
# Active signals: the interrupt hint (older builds), the live elapsed timer "(22s·"
# OR "(4m 50s·" (minutes+seconds), or a spinner verb ending in "…" (incl. hyphenated
# ones like "Razzle-dazzling…"). Completed timers read "…dazzled for 4m 50s" — no
# open-paren and no trailing "…", so they don't match.
_ACTIVE = re.compile(r'esc to interrupt|\(\d+[ms]\b|[A-Z][a-zA-Z-]{3,}…')
_PLACEHOLDER = re.compile(r'^(Try |Ask |Write |/ for |>_|Update )', re.I)
_PROMPT = re.compile(r'^[❯›](.*)$')
# statusline junk for the ghost fallback: "branch | +12 -3 | 8h ago", "| $4.20 |", "| 41% |"
# plus Claude Code's own feedback survey, which is not a question FROM your session.
_STATUSLINE = re.compile(r'\|\s*[+\-]?\d|\bago\b|\$\d|\d+%|agents:|' + HOST + r':|Opus|Sonnet|Haiku'
                         r'|1M context|auto mode|bypass permissions'
                         r'|How is Claude doing this session|\d:\s*(Bad|Fine|Good)\b|Dismiss')

_VERB = re.compile(r'([A-Z][a-zA-Z-]+…)')   # spinner verb, e.g. "Caramelizing…", "Razzle-dazzling…"

def _pane(name):
    raw = re.sub(r'\x1b\[[0-9;]*m', '', _tmux("capture-pane", "-p", "-t", name, "-S", "-120"))
    lines = raw.splitlines()
    # the spinner sits ~7 lines up (above the input box + 3-line statusline), so scan deeper
    working = any(_ACTIVE.search(l) for l in lines[-12:])
    verb = ""
    if working:
        for l in reversed(lines[-12:]):
            m = _VERB.search(l)
            if m:
                verb = m.group(1)   # includes the trailing …
                break
    typed = None
    for l in reversed(lines):
        s = l.strip().strip('│').strip()
        m = _PROMPT.match(s)
        if m:
            after = m.group(1).strip()
            if after and not _PLACEHOLDER.match(after):
                typed = after[:240]
            break
    return working, typed, verb

# Claude Code renders each assistant message starting with ● (or ⏺). A tool call
# is "● Bash(...)" / "● Read(...)"; a spoken message is "● <prose>". For a ghost
# (no transcript) the last spoken ● block, down to the input box, IS the last
# message — the same thing you see when you attach.
_MARKER = re.compile(r'^\s*[●⏺]\s+(.*)$')
_TOOLCALL = re.compile(r'^\s*[●⏺]\s+[A-Z]\w*\(')      # ● Bash( / ● Read( / ● Update(
_BOXEND = re.compile(r'^[❯›]|^─{5,}|^╭|^╰|' + HOST + r':')

def _ghost_full(name):
    raw = re.sub(r'\x1b\[[0-9;]*m', '', _tmux("capture-pane", "-p", "-t", name, "-S", "-600"))
    lines = raw.split("\n")
    # index of the last SPOKEN assistant marker (skip tool-call markers)
    start = None
    for i, l in enumerate(lines):
        if _MARKER.match(l) and not _TOOLCALL.match(l):
            start = i
    if start is None:
        # no marker in view: fall back to the last few non-chrome lines
        body = [re.sub(r'\s+', ' ', l.strip()) for l in lines
                if l.strip() and not _STATUSLINE.search(l) and not _PROMPT.match(l.strip())]
        if not body:
            return "", "", False, ""
        ask, is_q = _extract_ask("\n".join(body[-4:]))
        return "\n".join(body[-8:]), ask, is_q, ""   # 4-tuple: (full, ask, is_q, you)
    # end = the input box / separator / statusline after the message
    end = len(lines)
    for i in range(start + 1, len(lines)):
        s = lines[i].strip()
        if _BOXEND.match(s) or _STATUSLINE.search(lines[i]):
            end = i
            break
    block = lines[start:end]
    block[0] = _MARKER.match(block[0]).group(1)        # drop the ● marker itself
    # dedent + drop blank/box-only lines
    clean = [re.sub(r'\s+$', '', l) for l in block]
    common = min((len(l) - len(l.lstrip()) for l in clean if l.strip()), default=0)
    clean = [l[common:] if len(l) >= common else l for l in clean]
    clean = [l for l in clean if l.strip() and not re.match(r'^[─╭╰│]+$', l.strip())
             and not _NOISE.search(l)]
    full = "\n".join(clean)
    if not full.strip():          # e.g. the block was only the feedback survey
        return "", "", False, ""
    ask, is_q = _extract_ask(full)
    # the prompt before this answer: nearest "❯ text" above the block
    you = ""
    for i in range(start - 1, -1, -1):
        if _MARKER.match(lines[i]):
            break                 # hit the previous assistant message; stop
        m = re.match(r'^\s*[❯›]\s+(.+)$', lines[i])
        if m and m.group(1).strip() and not _NOISE.search(m.group(1)):
            you = re.sub(r'\s+', ' ', m.group(1).strip())
            break
    return full, ask, is_q, you

# ── assemble ──────────────────────────────────────────────────────────────
def _epoch(iso):
    if not iso:
        return 0
    try:
        import datetime as _dt
        d = _dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return int(d.timestamp())
    except Exception:
        return 0

def snapshot():
    # Demo mode: return fixture data instead of reading live tmux, so the floor can
    # be shown or screenshotted with no real sessions. OFFICE_DEMO=1 loads demo.json
    # next to this file; OFFICE_DEMO=/path/to/file.json loads that instead.
    _demo = os.environ.get("OFFICE_DEMO")
    if _demo:
        p = _demo if os.path.isfile(_demo) else os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo.json")
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return []
        now = int(time.time())
        for m in data:              # "ago_s": N  ->  ts N seconds ago, so the demo always looks fresh
            if "ago_s" in m:
                m["ts"] = now - int(m.pop("ago_s"))
        return data
    idx = _transcript_index()
    team = []
    now = int(time.time())
    for name, (attached, path, activity, created) in sessions().items():
        person, project = split_name(name)
        working, typed, verb = _pane(name)
        rec = _by_dir(path) or _join(path, idx, project)

        # ghost = no transcript AND not freshly created (a new session just hasn't
        # written a transcript yet — it's not a retention-deleted ghost).
        ghost = (rec is None) and (now - created > GHOST_AFTER)
        ask = ""; is_q = False; full = ""; you = ""
        if rec and rec["asst"]:
            full = rec["asst"]
            ask, is_q = _extract_ask(full)
            you = rec.get("you") or ""
            role = rec["role"]
        else:
            # no transcript, OR transcript exists but has no readable assistant text
            # (e.g. the session was just interrupted) -> read the pane instead.
            full, ask, is_q, gyou = _ghost_full(name)
            you = (rec.get("you") if rec else "") or gyou
            role = (rec["role"] if rec else None) or ("assistant" if ask else None)

        # classify (priority order). "working" means ONLY: a spinner is running on
        # screen right now (Claude is actively churning). Nothing else counts as working.
        # three states, in priority order:
        if working:
            state = "working"        # 🔵 esc-to-interrupt on screen — actively churning NOW
        elif typed or is_q:
            state = "waiting"        # 🖐 your turn — a question to answer OR a draft to send
        else:
            state = "review"         # 🟢 finished — your move, nothing specific queued

        team.append({
            "session": name, "person": person, "project": project,
            "attached": attached, "state": state,
            "ask": ask, "full": full[-2000:] if full else "",
            "you": you[:400] if you else "",
            "options": _options(ask, is_q) if (is_q and not typed) else [],
            "typed": typed, "ghost": ghost, "verb": verb if state == "working" else "",
            # recency for sort + the "ago" label: the time of the last real message in the
            # matched transcript — your prompt OR Claude's reply. It advances only on an actual
            # exchange, never on merely opening/attaching the tmux session. Falls back to the
            # session's own activity only when no transcript is matched yet.
            "ts": _epoch(rec.get("ts") if rec else None) or activity,
        })

    rank = {"waiting": 0, "typed": 1, "review": 2, "working": 3, "idle": 4}
    team.sort(key=lambda t: (rank.get(t["state"], 9), t["person"] or t["project"]))
    return team
