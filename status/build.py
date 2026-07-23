#!/usr/bin/env python3
"""Aggregate Claude Code session + agent data into data.json (HTML fetches it live)."""
import json, os, sys, datetime as dt
from collections import defaultdict, Counter
from pathlib import Path

ROOT = Path.home() / ".claude"
PROJECTS = ROOT / "projects"
LAST_USAGE = ROOT / "last-usage.json"      # snapshot dropped by statusline
OUT_DIR = Path(__file__).resolve().parent
DATA_OUT = OUT_DIR / "data.json"
HOME_FRAG = str(Path.home()).strip("/")    # e.g. "home/user" — stripped from project display paths

# Rough public API pricing (USD per 1M tokens)
COST = {
    "opus":   {"in": 15.00, "cache_w": 18.75, "cache_r": 1.50, "out": 75.00},
    "sonnet": {"in":  3.00, "cache_w":  3.75, "cache_r": 0.30, "out": 15.00},
    "haiku":  {"in":  1.00, "cache_w":  1.25, "cache_r": 0.10, "out":  5.00},
}
def fam(m):
    m = (m or "").lower()
    if "opus" in m: return "opus"
    if "sonnet" in m: return "sonnet"
    if "haiku" in m: return "haiku"
    return "opus"

def price(fam_, u):
    c = COST[fam_]
    ti = int(u.get("input_tokens") or 0)
    to = int(u.get("output_tokens") or 0)
    tcw = int(u.get("cache_creation_input_tokens") or 0)
    tcr = int(u.get("cache_read_input_tokens") or 0)
    return (ti*c["in"] + to*c["out"] + tcw*c["cache_w"] + tcr*c["cache_r"]) / 1_000_000, ti, to, tcw, tcr

def project_display(dirname):
    s = dirname.lstrip("-").replace("-", "/")
    if s.startswith(HOME_FRAG + "/"):
        s = s[len(HOME_FRAG) + 1:]
    return s or "home"

def parse_ts(s):
    if not s: return None
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except Exception:
        return None

# Agent flavor: emoji + role blurb for each known agent type
AGENT_FLAVOR = {
    "Explore":                     ("🔎", "Codebase scout — greps, globs, reads to find things"),
    "general-purpose":             ("🧰", "Handyman — open-ended research and multi-step work"),
    "Plan":                        ("📐", "Architect — designs implementation plans"),
    "claude-code-guide":           ("📚", "Docs librarian — Claude Code/API questions"),
    "statusline-setup":            ("🎛️", "Configures the statusline"),
    "market-researcher":           ("📊", "Market analyst"),
    "market-researcher-rigorous":  ("📈", "Deep-dive market analyst"),
    "market-researcher-lite":      ("📉", "Quick market pass"),
    "unknown":                     ("👤", "Unknown agent type"),
}
def agent_flavor(name):
    return AGENT_FLAVOR.get(name, ("🤖", "Custom agent"))

# ── Pass 1: walk every session ───────────────────────────────────────────
sessions = []
projects = defaultdict(lambda: {
    "name": "", "sessions": 0, "msgs_user": 0, "msgs_asst": 0,
    "tokens_in": 0, "tokens_out": 0, "tokens_cache_w": 0, "tokens_cache_r": 0,
    "cost": 0.0, "first": None, "last": None, "models": Counter(),
    "days": set(),           # unique active days
    "by_day": Counter(),     # day -> total msgs (for sparkline)
    "last_prompt": None,     # (ts_iso, text)
    "agents_used": Counter(),
})
by_day = defaultdict(lambda: {"user": 0, "asst": 0, "cost": 0.0, "tokens": 0})
by_hour = Counter()
recent_prompts = []
model_totals = Counter()

# Agent aggregation: keyed by subagent_type
agents = defaultdict(lambda: {
    "name": "", "invocations": 0, "tokens": 0, "cost": 0.0,
    "first": None, "last": None,
    "projects": Counter(),
    "models": Counter(),
})

for pdir in (sorted(PROJECTS.iterdir()) if PROJECTS.is_dir() else []):
    if not pdir.is_dir(): continue
    pname = project_display(pdir.name)
    for jf in pdir.glob("*.jsonl"):
        sid = jf.stem
        sub_dir = pdir / sid / "subagents"
        sub_files = sorted(sub_dir.glob("*.jsonl")) if sub_dir.is_dir() else []

        # ── Parse parent jsonl: collect Task tool_use invocations for agent typing ──
        task_calls = []  # list of (ts, subagent_type)
        s_user = s_asst = 0
        t_in = t_out = t_cw = t_cr = 0
        s_cost = 0.0
        s_first = s_last = None
        s_models = Counter()
        s_prompts = []

        for line in jf.open(encoding="utf-8", errors="replace"):
            try: d = json.loads(line)
            except Exception: continue
            ts = parse_ts(d.get("timestamp"))
            if ts:
                if s_first is None or ts < s_first: s_first = ts
                if s_last  is None or ts > s_last:  s_last  = ts
            t = d.get("type")
            if t == "user":
                msg = d.get("message", {})
                content = msg.get("content")
                text = ""
                if isinstance(content, str):
                    text = content.strip()
                elif isinstance(content, list):
                    text = " ".join(c.get("text","") for c in content if isinstance(c,dict) and c.get("type")=="text").strip()
                if text:
                    s_user += 1
                    if ts:
                        day = ts.strftime("%Y-%m-%d")
                        by_day[day]["user"] += 1
                        by_hour[ts.hour] += 1
                        projects[pname]["by_day"][day] += 1
                        projects[pname]["days"].add(day)
                    s_prompts.append((ts, text[:280]))
                    # Track project's most-recent user prompt
                    ts_iso = ts.isoformat() if ts else None
                    lp = projects[pname]["last_prompt"]
                    if ts_iso and (lp is None or ts_iso > lp[0]):
                        projects[pname]["last_prompt"] = (ts_iso, text[:220])
            elif t == "assistant":
                msg = d.get("message", {})
                model = msg.get("model") or ""
                s_models[model] += 1
                model_totals[model] += 1
                s_asst += 1
                u = msg.get("usage") or {}
                c_line, ti, to, tcw, tcr = price(fam(model), u)
                t_in += ti; t_out += to; t_cw += tcw; t_cr += tcr
                s_cost += c_line
                if ts:
                    day = ts.strftime("%Y-%m-%d")
                    by_day[day]["asst"] += 1
                    by_day[day]["cost"] += c_line
                    by_day[day]["tokens"] += ti + to + tcw + tcr
                    projects[pname]["by_day"][day] += 1
                    projects[pname]["days"].add(day)
                # Detect Task tool_use for agent typing
                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") in ("Task","Agent"):
                            st = (block.get("input") or {}).get("subagent_type") or "unknown"
                            task_calls.append((ts, st))

        # ── Parse subagent jsonls: attribute tokens/cost to session + agent ──
        # Correlate subagent -> agent type by matching the sub's first message timestamp
        # to the closest earlier Task call in the parent.
        for sf in sub_files:
            sub_first = None
            sub_tokens = 0
            sub_cost = 0.0
            sub_models = Counter()
            for line in sf.open(encoding="utf-8", errors="replace"):
                try: d = json.loads(line)
                except Exception: continue
                ts = parse_ts(d.get("timestamp"))
                if ts:
                    if sub_first is None or ts < sub_first: sub_first = ts
                    if s_first is None or ts < s_first: s_first = ts
                    if s_last  is None or ts > s_last:  s_last  = ts
                t = d.get("type")
                if t == "assistant":
                    msg = d.get("message", {})
                    model = msg.get("model") or ""
                    s_models[model] += 1
                    sub_models[model] += 1
                    model_totals[model] += 1
                    s_asst += 1
                    u = msg.get("usage") or {}
                    c_line, ti, to, tcw, tcr = price(fam(model), u)
                    t_in += ti; t_out += to; t_cw += tcw; t_cr += tcr
                    s_cost += c_line
                    sub_cost += c_line
                    sub_tokens += ti + to + tcw + tcr
                    if ts:
                        day = ts.strftime("%Y-%m-%d")
                        by_day[day]["asst"] += 1
                        by_day[day]["cost"] += c_line
                        by_day[day]["tokens"] += ti + to + tcw + tcr

            # Which agent type made this subagent? Match closest earlier task call.
            atype = "unknown"
            if sub_first and task_calls:
                candidates = [(ts, st) for ts, st in task_calls if ts and ts <= sub_first]
                if candidates:
                    atype = candidates[-1][1]  # last (closest) earlier call
                else:
                    atype = task_calls[0][1]   # fallback: first call
            a = agents[atype]
            a["name"] = atype
            a["invocations"] += 1
            a["tokens"] += sub_tokens
            a["cost"] += sub_cost
            if sub_first:
                iso = sub_first.isoformat()
                if a["first"] is None or iso < a["first"]: a["first"] = iso
                if a["last"]  is None or iso > a["last"]:  a["last"]  = iso
            a["projects"][pname] += 1
            projects[pname]["agents_used"][atype] += 1
            for m, n in sub_models.items(): a["models"][m] += n

        if s_asst == 0 and s_user == 0:
            continue
        sessions.append({
            "id": sid, "project": pname,
            "user": s_user, "asst": s_asst,
            "in": t_in, "out": t_out, "cw": t_cw, "cr": t_cr,
            "cost": s_cost,
            "first": s_first.isoformat() if s_first else None,
            "last":  s_last.isoformat()  if s_last  else None,
            "models": dict(s_models),
        })
        p = projects[pname]
        p["name"] = pname
        p["sessions"] += 1
        p["msgs_user"] += s_user
        p["msgs_asst"] += s_asst
        p["tokens_in"] += t_in
        p["tokens_out"] += t_out
        p["tokens_cache_w"] += t_cw
        p["tokens_cache_r"] += t_cr
        p["cost"] += s_cost
        for m, n in s_models.items(): p["models"][m] += n
        if s_first and (p["first"] is None or s_first < parse_ts(p["first"])):
            p["first"] = s_first.isoformat()
        if s_last and (p["last"] is None or s_last > parse_ts(p["last"])):
            p["last"] = s_last.isoformat()
        for ts, text in s_prompts:
            if ts:
                recent_prompts.append((ts.isoformat(), pname, text, sid))

# ── Include user-defined agents that were never invoked (empty desks) ──
user_agent_dir = ROOT / "agents"
if user_agent_dir.is_dir():
    for md in user_agent_dir.glob("*.md"):
        name = md.stem
        if name not in agents:
            agents[name] = {
                "name": name, "invocations": 0, "tokens": 0, "cost": 0.0,
                "first": None, "last": None,
                "projects": Counter(), "models": Counter(), "user_defined": True,
            }
        else:
            agents[name]["user_defined"] = True

# ── Team view: personify each project as an "employee" ──
# Deterministic emoji per project based on name — first hit wins.
EMOJI_HINTS = [
    ("api",    "🔌"), ("backend","🔌"), ("app",   "📱"),
    ("web",    "🌐"), ("site",   "🌐"), ("bot",   "🤖"),
    ("docs",   "📄"), ("book",   "📖"), ("data",  "📊"),
    ("planning","🗓️"), ("random", "🎲"),
]
def project_emoji(name):
    n = name.lower()
    for k, e in EMOJI_HINTS:
        if k in n: return e
    return "💼"

def short_name(name):
    parts = name.split("/")
    if len(parts) <= 2: return name
    # collapse to last two segments for readability
    return "/".join(parts[-2:])

def status_for(iso_last):
    if not iso_last: return "dormant"
    last = parse_ts(iso_last)
    if last is None: return "dormant"
    age = (dt.datetime.now(dt.timezone.utc) - last).total_seconds()
    if age < 3600:   return "working"
    if age < 86400:  return "active"
    if age < 604800: return "idle"
    if age < 2592000: return "napping"
    return "dormant"

today_d = dt.date.today()
team = []
max_project_cost = max((p["cost"] for p in projects.values()), default=1) or 1
for p in projects.values():
    if p["sessions"] == 0: continue
    # 14-day sparkline
    spark = []
    for i in range(13, -1, -1):
        day = (today_d - dt.timedelta(days=i)).strftime("%Y-%m-%d")
        spark.append(p["by_day"].get(day, 0))
    # supporting agents (top 3)
    top_agents = []
    for aname, ct in p["agents_used"].most_common(4):
        emoji, _ = agent_flavor(aname)
        top_agents.append({"name": aname, "emoji": emoji, "count": ct})
    team.append({
        "name": p["name"],
        "short": short_name(p["name"]),
        "emoji": project_emoji(p["name"]),
        "status": status_for(p["last"]),
        "sessions": p["sessions"],
        "msgs": p["msgs_user"] + p["msgs_asst"],
        "user_msgs": p["msgs_user"],
        "tokens": p["tokens_in"] + p["tokens_out"] + p["tokens_cache_w"] + p["tokens_cache_r"],
        "cost": p["cost"],
        "cost_pct": (p["cost"] / max_project_cost) if max_project_cost else 0,
        "first": p["first"],
        "last": p["last"],
        "active_days": len(p["days"]),
        "spark": spark,
        "spark_max": max(spark) if spark else 0,
        "last_prompt_ts": p["last_prompt"][0] if p["last_prompt"] else None,
        "last_prompt": p["last_prompt"][1] if p["last_prompt"] else None,
        "support": top_agents,
        "models": dict(p["models"]),
    })
_status_rank = {"working":0,"active":1,"idle":2,"napping":3,"dormant":4}
team.sort(key=lambda t: (_status_rank[t["status"]], -t["cost"]))

# ── Sort ──
sessions.sort(key=lambda s: s["last"] or "", reverse=True)
project_list = sorted(projects.values(), key=lambda p: p["cost"], reverse=True)
recent_prompts.sort(reverse=True)
recent_prompts = recent_prompts[:80]

# ── Agent office view: sort by activity ──
def agent_status(a):
    if a["last"] is None: return "ghost"
    last = parse_ts(a["last"])
    if last is None: return "ghost"
    age_s = (dt.datetime.now(dt.timezone.utc) - last).total_seconds()
    if age_s < 3600: return "working"      # last hour
    if age_s < 86400: return "active"       # last 24h
    if age_s < 604800: return "idle"        # last 7d
    return "napping"

max_invocations = max((a["invocations"] for a in agents.values()), default=1) or 1
office = []
for a in agents.values():
    st = agent_status(a)
    emoji, role = agent_flavor(a["name"])
    top_project = a["projects"].most_common(1)[0][0] if a["projects"] else None
    top_project_ct = a["projects"].most_common(1)[0][1] if a["projects"] else 0
    office.append({
        "name": a["name"],
        "emoji": emoji,
        "role": role,
        "invocations": a["invocations"],
        "tokens": a["tokens"],
        "cost": a["cost"],
        "first": a["first"],
        "last":  a["last"],
        "status": st,
        "top_project": top_project,
        "top_project_ct": top_project_ct,
        "projects_count": len(a["projects"]),
        "activity_pct": (a["invocations"] / max_invocations) if max_invocations else 0,
        "user_defined": a.get("user_defined", False),
        "models": dict(a["models"]),
    })
# Order: working → active → idle → napping → ghost, within each by invocations desc
status_order = {"working":0, "active":1, "idle":2, "napping":3, "ghost":4}
office.sort(key=lambda a: (status_order[a["status"]], -a["invocations"], a["name"]))

# ── Totals ──
tot = {
    "sessions": len(sessions),
    "projects": len([p for p in project_list if p["sessions"] > 0]),
    "user": sum(s["user"] for s in sessions),
    "asst": sum(s["asst"] for s in sessions),
    "in":   sum(s["in"] for s in sessions),
    "out":  sum(s["out"] for s in sessions),
    "cw":   sum(s["cw"] for s in sessions),
    "cr":   sum(s["cr"] for s in sessions),
    "cost": sum(s["cost"] for s in sessions),
}

# Streak & activity series
active_days = sorted(by_day.keys())
today = dt.date.today()
streak = 0
d = today
while d.strftime("%Y-%m-%d") in by_day:
    streak += 1
    d -= dt.timedelta(days=1)
series = []
for i in range(29, -1, -1):
    day = (today - dt.timedelta(days=i)).strftime("%Y-%m-%d")
    b = by_day.get(day, {"user":0,"asst":0,"cost":0.0,"tokens":0})
    series.append({"day": day, **b})
hour_series = [by_hour.get(h, 0) for h in range(24)]
model_break = [{"model": m, "asst_msgs": n} for m, n in model_totals.most_common()]

# ── Live rate limits from statusline snapshot ──
live = None
if LAST_USAGE.exists():
    try:
        snap = json.loads(LAST_USAGE.read_text())
        mtime = dt.datetime.fromtimestamp(LAST_USAGE.stat().st_mtime, tz=dt.timezone.utc)
        rl = snap.get("rate_limits") or {}
        five = rl.get("five_hour") or {}
        week = rl.get("seven_day") or rl.get("weekly") or {}
        week_opus = rl.get("seven_day_opus") or rl.get("weekly_opus") or {}
        def resets_iso(v):
            if v is None: return None
            try:
                return dt.datetime.fromtimestamp(int(v), tz=dt.timezone.utc).isoformat()
            except Exception:
                return str(v)
        ctx = snap.get("context_window") or {}
        cost = snap.get("cost") or {}
        model = (snap.get("model") or {}).get("display_name")
        live = {
            "captured_at": mtime.isoformat(),
            "captured_age_s": (dt.datetime.now(dt.timezone.utc) - mtime).total_seconds(),
            "model": model,
            "cwd": snap.get("cwd"),
            "session_id": snap.get("session_id"),
            "context_used_pct": ctx.get("used_percentage"),
            "context_tokens": ctx.get("total_input_tokens") or ctx.get("input_tokens"),
            "context_window_size": ctx.get("context_window_size"),
            "five_hour_used_pct": five.get("used_percentage"),
            "five_hour_resets_at": resets_iso(five.get("resets_at")),
            "weekly_used_pct": week.get("used_percentage"),
            "weekly_resets_at": resets_iso(week.get("resets_at")),
            "weekly_opus_used_pct": week_opus.get("used_percentage"),
            "weekly_opus_resets_at": resets_iso(week_opus.get("resets_at")),
            "session_cost_usd": cost.get("total_cost_usd"),
            "session_duration_ms": cost.get("total_duration_ms"),
            "lines_added": cost.get("total_lines_added"),
            "lines_removed": cost.get("total_lines_removed"),
        }
    except Exception as e:
        live = {"error": str(e)}

data = {
    "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    "totals": tot,
    "projects": [{k:v for k,v in dict(p, models=dict(p["models"])).items()
                  if k not in ("days","by_day","last_prompt","agents_used")}
                 for p in project_list],
    "team": team,
    "sessions_recent": sessions[:60],
    "recent_prompts": recent_prompts,
    "series_30d": series,
    "hour_series": hour_series,
    "model_break": model_break,
    "streak": streak,
    "active_days_total": len(active_days),
    "first_ever": active_days[0] if active_days else None,
    "office": office,
    "live": live,
}
DATA_OUT.write_text(json.dumps(data, default=str))
print(f"wrote {DATA_OUT} ({DATA_OUT.stat().st_size//1024} KB) · sessions={tot['sessions']} agents={len(office)} live={'yes' if live and 'error' not in live else 'no'}")
