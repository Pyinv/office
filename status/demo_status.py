"""Fictional status data for OFFICE_DEMO — matches build.py's data.json schema.

Every number here is invented. It is anchored to now() so the demo dashboard
always looks current, and seeded so it's stable across reloads. Nothing real.
"""
import datetime as dt
import random

_MODELS = ["claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5-20251001"]

# (project, cost, emoji, status) — fictional clients/ventures/personal, mirrors demo.json
_PROJECTS = [
    ("clients/cardiobeacon-mdr", 1460.0, "🫀", "working"),
    ("clients/gridguard-smgw",   1185.0, "⚡", "working"),
    ("clients/hanseat-finanz-dsgvo", 970.0, "💶", "active"),
    ("ventures/kritis-copilot",    905.0, "🤖", "active"),
    ("clients/aquavolt-scada",    655.0, "💧", "active"),
    ("clients/marktplatz24-checkout", 540.0, "🛒", "idle"),
    ("ventures/auditbahn-isms",     485.0, "🛡️", "working"),
    ("clients/praezision-mes",     430.0, "⚙️", "idle"),
    ("clients/dentalflow-records", 305.0, "🦷", "napping"),
    ("personal/smart-home",          88.0, "🏠", "idle"),
    ("personal/reading-list",        34.0, "📚", "napping"),
]

_PROMPTS = {
    "clients/cardiobeacon-mdr": "the BLE pairing can fail silently — how do we treat that in the risk file?",
    "clients/gridguard-smgw": "wire the smart-meter gateway to the GWA over TLS with the BSI cert profile",
    "clients/hanseat-finanz-dsgvo": "scan the data flows for anything that leaves the EU",
    "ventures/kritis-copilot": "ground the answers only in the BSI docs we ingested",
    "clients/aquavolt-scada": "the operators want remote access to the pump SCADA — is that safe?",
    "clients/marktplatz24-checkout": "the checkout drops ~8% at the payment step",
    "ventures/auditbahn-isms": "map our controls to ISO 27001 Annex A automatically",
    "clients/praezision-mes": "add offline-first sync so the shop floor keeps running if the network drops",
    "clients/dentalflow-records": "encrypt the patient-record store at rest with per-practice keys",
    "personal/smart-home": "make the garden watering skip if rain is forecast",
    "personal/reading-list": "turn my half-finished books into a weekly nudge",
}

_STATUS_AGE = {"working": 1800, "active": 40000, "idle": 300000, "napping": 1500000, "dormant": 4000000}


def _iso(ts):
    return ts.isoformat()


def build():
    rnd = random.Random(20260720)
    now = dt.datetime.now(dt.timezone.utc)
    today = now.date()

    projects, team, sessions = [], [], []
    by_day = {}          # day -> {user, asst, cost, tokens}
    by_hour = [0] * 24
    model_totals = {m: 0 for m in _MODELS}
    recent = []

    for pname, cost, emoji, status in _PROJECTS:
        n_sess = rnd.randint(1, 5)
        asst = int(cost * rnd.uniform(2.2, 3.4))
        user = int(asst * rnd.uniform(0.10, 0.18)) + 3
        cr = int(cost * rnd.uniform(380_000, 520_000))   # cache reads dominate
        cw = int(cr * rnd.uniform(0.02, 0.05))
        ti = int(cost * rnd.uniform(9_000, 16_000))
        to = int(cost * rnd.uniform(2_800, 5_200))
        last = now - dt.timedelta(seconds=_STATUS_AGE[status] * rnd.uniform(0.6, 1.4))
        first = now - dt.timedelta(days=rnd.randint(24, 130))
        # models mix per project
        mm = {}
        mm["claude-opus-4-8"] = int(asst * rnd.uniform(0.55, 0.8))
        mm["claude-sonnet-5"] = int(asst * rnd.uniform(0.1, 0.25))
        mm["claude-haiku-4-5-20251001"] = max(0, asst - mm["claude-opus-4-8"] - mm["claude-sonnet-5"])
        for m, c in mm.items():
            model_totals[m] += c

        # 14-day sparkline + fold activity into by_day/by_hour
        spark = []
        for i in range(13, -1, -1):
            day = (today - dt.timedelta(days=i))
            dkey = day.strftime("%Y-%m-%d")
            # more recent + higher-cost projects are busier
            base = (cost / 1500) * (1.0 if i < 8 else 0.5)
            m = max(0, int(rnd.gauss(base * 6, base * 3)))
            spark.append(m)
            if m:
                b = by_day.setdefault(dkey, {"user": 0, "asst": 0, "cost": 0.0, "tokens": 0})
                b["asst"] += m
                b["user"] += max(1, m // 8)
                b["cost"] += cost / 14 * rnd.uniform(0.7, 1.3)
                b["tokens"] += int((cr + cw + ti + to) / 14)
        active_days = sum(1 for x in spark if x) + rnd.randint(2, 10)

        projects.append({
            "name": pname, "sessions": n_sess, "msgs_user": user, "msgs_asst": asst,
            "tokens_in": ti, "tokens_out": to, "tokens_cache_w": cw, "tokens_cache_r": cr,
            "cost": round(cost, 2), "first": _iso(first), "last": _iso(last), "models": mm,
        })
        team.append({
            "name": pname, "short": pname.split("/")[-1], "emoji": emoji, "status": status,
            "sessions": n_sess, "msgs": user + asst, "user_msgs": user,
            "tokens": ti + to + cw + cr, "cost": round(cost, 2), "cost_pct": 0.0,
            "first": _iso(first), "last": _iso(last), "active_days": active_days,
            "spark": spark, "spark_max": max(spark) if spark else 0,
            "last_prompt_ts": _iso(last), "last_prompt": _PROMPTS.get(pname, ""),
            "support": [], "models": mm,
        })
        sessions.append({
            "id": pname.replace("/", "-") + "-" + str(rnd.randint(1000, 9999)),
            "project": pname, "user": user, "asst": asst,
            "in": ti, "out": to, "cw": cw, "cr": cr, "cost": round(cost, 2),
            "first": _iso(first), "last": _iso(last), "models": mm,
        })
        recent.append([_iso(last), pname, _PROMPTS.get(pname, ""), sessions[-1]["id"]])

    maxc = max((p["cost"] for p in projects), default=1) or 1
    for t in team:
        t["cost_pct"] = t["cost"] / maxc
    _rank = {"working": 0, "active": 1, "idle": 2, "napping": 3, "dormant": 4}
    team.sort(key=lambda t: (_rank.get(t["status"], 9), -t["cost"]))
    projects.sort(key=lambda p: p["cost"], reverse=True)
    sessions.sort(key=lambda s: s["last"], reverse=True)
    recent.sort(reverse=True)

    # 30-day activity series
    series = []
    for i in range(29, -1, -1):
        day = (today - dt.timedelta(days=i)).strftime("%Y-%m-%d")
        b = by_day.get(day, {"user": 0, "asst": 0, "cost": 0.0, "tokens": 0})
        series.append({"day": day, "user": b["user"], "asst": b["asst"],
                       "cost": round(b["cost"], 2), "tokens": b["tokens"]})
    # activity by hour (work-day shaped)
    shape = [1, 1, 0, 0, 0, 0, 1, 2, 4, 7, 9, 8, 6, 7, 9, 10, 8, 6, 5, 6, 5, 4, 2, 1]
    hour_series = [int(s * rnd.uniform(10, 18)) for s in shape]

    tot = {
        "sessions": len(sessions),
        "projects": len(projects),
        "user": sum(s["user"] for s in sessions),
        "asst": sum(s["asst"] for s in sessions),
        "in": sum(s["in"] for s in sessions),
        "out": sum(s["out"] for s in sessions),
        "cw": sum(s["cw"] for s in sessions),
        "cr": sum(s["cr"] for s in sessions),
        "cost": round(sum(s["cost"] for s in sessions), 2),
    }
    model_break = [{"model": m, "asst_msgs": c} for m, c in
                   sorted(model_totals.items(), key=lambda kv: -kv[1]) if c]

    office = [
        {"name": "Explore", "emoji": "🔎", "role": "Codebase scout", "invocations": 128,
         "tokens": 9_400_000, "cost": 214.0, "status": "working",
         "first": _iso(now - dt.timedelta(days=90)), "last": _iso(now - dt.timedelta(minutes=12)),
         "top_project": "clients/cardiobeacon-mdr", "top_project_ct": 41,
         "models": {"claude-haiku-4-5-20251001": 90, "claude-opus-4-8": 38}},
        {"name": "general-purpose", "emoji": "🧰", "role": "Handyman — research + multi-step work",
         "invocations": 76, "tokens": 15_800_000, "cost": 402.0, "status": "active",
         "first": _iso(now - dt.timedelta(days=110)), "last": _iso(now - dt.timedelta(hours=5)),
         "top_project": "ventures/kritis-copilot", "top_project_ct": 22,
         "models": {"claude-opus-4-8": 64, "claude-sonnet-5": 12}},
        {"name": "Plan", "emoji": "📐", "role": "Architect — designs implementation plans",
         "invocations": 44, "tokens": 6_100_000, "cost": 176.0, "status": "idle",
         "first": _iso(now - dt.timedelta(days=70)), "last": _iso(now - dt.timedelta(days=2)),
         "top_project": "clients/gridguard-smgw", "top_project_ct": 15,
         "models": {"claude-opus-4-8": 44}},
    ]

    def _resets(hours):
        return _iso(now + dt.timedelta(hours=hours))
    live = {
        "captured_at": _iso(now - dt.timedelta(seconds=30)),
        "captured_age_s": 30,
        "model": "Opus 4.8", "cwd": "~/develop", "session_id": "demo",
        "context_used_pct": 34, "context_tokens": 68000, "context_window_size": 200000,
        "five_hour_used_pct": 41, "five_hour_resets_at": _resets(2.3),
        "weekly_used_pct": 58, "weekly_resets_at": _resets(51),
        "weekly_opus_used_pct": 37, "weekly_opus_resets_at": _resets(51),
        "session_cost_usd": 3.74, "session_duration_ms": 4_260_000,
        "lines_added": 1284, "lines_removed": 393,
    }

    active_total = len({d for d in by_day})
    first_ever = min((p["first"] for p in projects), default=None)
    return {
        "generated": now.isoformat(timespec="seconds"),
        "totals": tot,
        "projects": projects,
        "team": team,
        "sessions_recent": sessions,
        "recent_prompts": recent[:80],
        "series_30d": series,
        "hour_series": hour_series,
        "model_break": model_break,
        "streak": 12,
        "active_days_total": max(active_total, 34),
        "first_ever": first_ever,
        "office": office,
        "live": live,
    }
