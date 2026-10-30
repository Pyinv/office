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
import subprocess, re, os, json, glob, time
from concurrent.futures import ThreadPoolExecutor

HOME = os.path.expanduser("~")
PROJ = os.path.join(HOME, ".claude", "projects")
# ~/.claude/projects encodes each cwd by replacing "/" with "-"; this is the
# key for the bare home dir, which holds no real session and is skipped.
HOME_KEY = HOME.replace("/", "-")
GHOST_AFTER = 3600   # a no-transcript session older than this is a ghost; newer = just-created

def _tmux(*a):
    try:
        return subprocess.run(["tmux", *a], capture_output=True, text=True).stdout
    except (FileNotFoundError, OSError):
        return ""      # no tmux (or it errored) -> empty; the floor just shows nothing

def sessions():
    out = _tmux("list-panes", "-a", "-F",
                "#{session_name}\t#{session_attached}\t#{pane_current_path}\t#{session_activity}\t#{session_created}\t#{pane_pid}")
    seen = {}
    for line in out.strip().splitlines():
        p = line.split("\t")
        if len(p) < 3:
            continue
        name, att, path = p[0], p[1], p[2]
        act = int(p[3]) if len(p) > 3 and p[3].isdigit() else 0   # tmux last-activity epoch
        crt = int(p[4]) if len(p) > 4 and p[4].isdigit() else 0   # tmux session-created epoch
        pid = int(p[5]) if len(p) > 5 and p[5].isdigit() else 0   # the pane's shell; Claude runs under it
        seen.setdefault(name, (att != "0", path, act, crt, pid))
    return seen

# ── process side: which transcript is THIS pane's Claude writing? ─────────
# Claude Code keeps ~/.claude/sessions/<pid>.json for each running process, naming its
# sessionId and start cwd. Walking from the pane's shell down to that process gives an
# exact pane -> transcript match, which the directory heuristics below can't when two
# sessions were launched in the same folder (they'd both show the newer transcript).
SESS_DIR = os.path.join(HOME, ".claude", "sessions")

def _children():
    kids = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat") as f:
                st = f.read()
            ppid = int(st[st.rindex(")") + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        kids.setdefault(ppid, []).append(int(d))
    return kids

# every transcript under ~/.claude/projects, by the first 8 characters of its name (the short session
# id). One scan serves a whole snapshot — globbing every project folder once per agent was most of
# the floor's time. Rebuilt when it is older than a couple of seconds, so a new transcript shows up.
_JIDX = {"at": 0.0, "idx": {}}

def _transcripts(sid):
    if time.time() - _JIDX["at"] > 2:
        idx = {}
        try:
            for d in os.scandir(PROJ):
                if not d.is_dir():
                    continue
                try:
                    for f in os.scandir(d.path):
                        if f.name.endswith(".jsonl"):
                            idx.setdefault(f.name[:8], []).append(f.path)
                except OSError:
                    pass
        except OSError:
            pass
        _JIDX.update(at=time.time(), idx=idx)
    return [p for p in _JIDX["idx"].get(sid[:8], []) if os.path.basename(p).startswith(sid)]

def _by_pid(pane_pid, kids):
    return _claude(pane_pid, kids)[1]

def _claude(pane_pid, kids):
    """The Claude Code running in this pane: its own status file (~/.claude/sessions/<pid>.json,
    which says busy / idle / waiting and what it waits for) and its transcript. (None, None) when
    no Claude runs there."""
    if not pane_pid:
        return None, None
    frontier, depth = [pane_pid], 0
    while frontier and depth < 4:               # shell -> claude (maybe via a wrapper or two)
        nxt = []
        for pid in frontier:
            sf = os.path.join(SESS_DIR, f"{pid}.json")
            if os.path.isfile(sf):
                try:
                    with open(sf) as f:
                        meta = json.load(f)
                    # newer Claude Code versions can switch transcripts under one process (a
                    # "parked job", a resume): the file names several ids — take whichever
                    # transcript was written to last, not just sessionId
                    sids = {str(v) for v in meta.values()                # full ids, or the 8-char short form
                            if isinstance(v, str) and re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f-]{27})?", v)}
                    hits = [h for sid in sids for h in _transcripts(sid)]
                    if hits:
                        hits.sort(key=lambda h: -os.path.getmtime(h))
                    return meta, (_parse_one(hits[0]) if hits else None)
                except (OSError, ValueError):
                    pass
            nxt.extend(kids.get(pid, []))
        frontier, depth = nxt, depth + 1
    return None, None

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
        return "📷 image" if _IMG_NOTE.match(c) else c
    if not isinstance(c, list):
        return ""
    out = []
    for b in c:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "image":
            out.append("📷 image")                  # a pasted/shared picture: say so, don't go blank
        elif b.get("type") == "text":
            t = b.get("text", "")
            if _IMG_NOTE.match(t):                 # the harness's "[Image: original 2160x480, displayed at …]" line
                continue
            out.append(t)
    return " ".join(out)

_IMG_NOTE = re.compile(r"^\s*\[Image: original \d+x\d+")

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
    open_tools = {}                      # tool_use id -> (name, input) still without a tool_result
    for line in tail:
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("cwd"):
            cwd = o["cwd"]
        t = o.get("type")
        content = (o.get("message") or {}).get("content") if t in ("assistant", "user") else None
        for b in content if isinstance(content, list) else ():
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                open_tools[b.get("id")] = (b.get("name") or "", b.get("input") or {})
            elif b.get("type") == "tool_result":
                open_tools.pop(b.get("tool_use_id"), None)
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
           "asst_ts": asst_ts, "you": you, "file": f, "open": _open_ask(open_tools)}
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

def _open_ask(open_tools):
    """What Claude is waiting on you for, read from the transcript itself, never from the wording:
    an AskUserQuestion still without its answer (question + its choices), or any other tool call
    still without its result (a permission prompt, once the pane shows no spinner)."""
    for name, inp in reversed(list(open_tools.values())):
        if name == "AskUserQuestion":
            qs = [q for q in (inp.get("questions") or []) if isinstance(q, dict)]
            if qs:
                q = qs[0]
                opts = [o.get("label", "") if isinstance(o, dict) else str(o) for o in (q.get("options") or [])]
                return {"kind": "question", "ask": str(q.get("question") or "").strip(),
                        "options": [o for o in opts if o], "more": len(qs) - 1}
    if open_tools:
        name, _ = list(open_tools.values())[-1]
        return {"kind": "permission", "ask": "Permission to use " + name, "options": [], "more": 0}
    return None

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
_VERB = re.compile(r'([A-Z][a-zA-Z-]+…)')   # spinner verb, e.g. "Caramelizing…", "Razzle-dazzling…"

_SGR = re.compile(r'\x1b\[[0-9;]*m')
# Claude Code pre-fills an idle input box with a DIM suggestion of your next prompt (SGR 2). It is
# not something you typed: drop dim runs before reading the box, or every idle agent looks like it
# holds an unsent draft (drops wait for nothing, a restart types the suggestion back in).
_DIM = re.compile(r'\x1b\[2m.*?(?=\x1b\[(?:0|22)?m|$)')

def _undim(line):
    return _SGR.sub('', _DIM.sub('', line))

def _pane(name):
    raw_e = _tmux("capture-pane", "-p", "-e", "-t", name, "-S", "-120")
    lines_e = raw_e.splitlines()
    lines = [_SGR.sub('', l) for l in lines_e]
    # the spinner sits ~7 lines up (above the input box + 3-line statusline), so scan deeper
    working = any(_ACTIVE.search(l) for l in lines[-12:])
    verb = ""
    if working:
        for l in reversed(lines[-12:]):
            m = _VERB.search(l)
            if m:
                verb = m.group(1)   # includes the trailing …
                break
    typed = None; draft = ""
    for i in range(len(lines) - 1, -1, -1):
        s = lines[i].strip().strip('│').strip()
        m = _PROMPT.match(s)
        if m:
            m = _PROMPT.match(_undim(lines_e[i]).strip().strip('│').strip()) or m
            after = m.group(1).strip()
            if after and not _PLACEHOLDER.match(after):
                typed = after[:240]
                # the whole draft: the prompt box runs from the ❯ line down to the next rule
                box = [after]
                for l in lines_e[i + 1:]:
                    t = _undim(l).strip().strip('│').strip()
                    if not t or t.startswith("─") or t.startswith("╰"):
                        break
                    box.append(t)
                draft = " ".join(box)[:4000]
            break
    tail = "\n".join(lines[-15:])
    update = "Restart to update" in tail                      # Claude Code installed a new version
    menu = len(_MENU.findall(tail)) >= 2                        # a pick-one question is open
    mode = next((v for k, v in _MODES if k in tail), "")       # the permission mode on the status line
    return working, typed, verb, update, menu, mode, draft

_MENU = re.compile(r"Enter to select|❯\s*\d+\.\s|Esc to cancel")
_MODES = (("auto mode on", "auto"), ("bypass permissions on", "bypassPermissions"),
          ("accept edits on", "acceptEdits"), ("plan mode on", "plan"))

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
    kids = _children()
    live = sessions()
    # one tmux capture per agent, side by side. Only the restart button reads it (a new Claude
    # Code waiting, the permission mode, a draft to retype); nothing on the floor is read off the screen.
    with ThreadPoolExecutor(max_workers=12) as ex:
        panes = dict(zip(live, ex.map(_pane, live)))
    for name, (attached, path, activity, created, pid) in live.items():
        person, project = split_name(name)
        _w, _t, _v, update, menu, mode, draft = panes[name]
        meta, rec = _claude(pid, kids)
        running = meta is not None
        rec = rec or _by_dir(path) or _join(path, idx, project)

        # ghost = no transcript AND not freshly created (a new session just hasn't
        # written a transcript yet — it's not a retention-deleted ghost).
        ghost = (rec is None) and (now - created > GHOST_AFTER)
        full = (rec or {}).get("asst") or ""
        you = (rec or {}).get("you") or ""

        # the state is what Claude Code itself writes to its status file: busy, waiting (for you:
        # a permission prompt, a question, a dialog) or idle. Not running = no status file.
        status = (meta or {}).get("status")
        state = {"busy": "working", "waiting": "waiting"}.get(status, "review")
        ask = None
        if state == "waiting":
            ask = (rec or {}).get("open")            # the open AskUserQuestion / tool call, from the transcript
            why = str((meta or {}).get("waitingFor") or "")
            if why == "permission prompt":
                tool = ask["ask"] if ask and ask["kind"] == "permission" else "Permission to use a tool"
                ask = {"kind": "permission", "ask": tool, "options": [], "more": 0}
            elif not (ask and ask["kind"] == "question"):
                ask = {"kind": "dialog", "ask": (why or "waiting").capitalize(), "options": [], "more": 0}

        team.append({
            "session": name, "person": person, "project": project,
            "dir": path, "attached": attached, "state": state,
            "ask": ask["ask"] if ask else "", "asks": ask["kind"] if ask else "",
            "more": ask["more"] if ask else 0, "full": full[-2000:] if full else "",
            "you": you[:400] if you else "",
            "options": ask["options"] if ask else [],
            "typed": None, "ghost": ghost, "running": running, "verb": "",
            # recency for sort + the "ago" label: the time of the last real message in the
            # matched transcript — your prompt OR Claude's reply. It advances only on an actual
            # exchange, never on merely opening/attaching the tmux session. Falls back to the
            # session's own activity only when no transcript is matched yet.
            "ts": _epoch(rec.get("ts") if rec else None) or activity,
            # the Claude conversation id (transcript file name): lets a dead session be
            # brought back with `claude --resume` instead of a blank start
            "sid": os.path.basename(rec["file"])[:-6] if rec and rec.get("file") else "",
            # for the restart button: a new Claude Code waits to be restarted into, a question
            # menu is open (a restart would lose it), and the permission mode to bring back
            "update": update, "menu": menu, "mode": mode, "draft": draft,
        })

    rank = {"waiting": 0, "typed": 1, "review": 2, "working": 3, "idle": 4}
    team.sort(key=lambda t: (rank.get(t["state"], 9), t["person"] or t["project"]))
    return team
