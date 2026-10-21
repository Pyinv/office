"""What the ⟳ Restore button may bring back after tmux dies (a crash or a reboot).

live.json keeps two sets:

    sessions   the agents alive under the CURRENT tmux server, rewritten on every floor poll
    baseline   the agents that were alive when the PREVIOUS tmux server went away, kept until
               each one is back, deliberately closed through the Office, or dismissed (Forget)

A new tmux server is recognised by its pid + start time. The moment one appears, everything the
old server had (plus any baseline not yet restored) becomes the baseline, so restoring one agent
by hand, or starting a fresh one, can no longer wipe the rest from the list.

    old server: {a, b, c}  ──tmux dies──▶  file still {a, b, c}       restore offers a, b, c
    new server: {a}        ──floor poll─▶  sessions {a}, baseline {b, c}   restore offers b, c
    Office kills b         ─────────────▶  baseline {c}                    restore offers c

Everything here is pure except server_id(); server.py does the file I/O.
"""
import subprocess


def server_id(socket=None):
    """pid:start-time of the running tmux server, "" when none is running."""
    cmd = ["tmux"] + (["-L", socket] if socket else []) + ["display-message", "-p", "#{pid}:#{start_time}"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
    except Exception:
        return ""
    sid = out.stdout.strip()
    return sid if out.returncode == 0 and ":" in sid else ""


def _norm(stored):
    stored = stored if isinstance(stored, dict) else {}
    return {"server": str(stored.get("server") or ""),
            "sessions": dict(stored.get("sessions") or {}),
            "baseline": dict(stored.get("baseline") or {})}


def merge(stored, cur, server):
    """The new live.json content after a floor poll that saw `cur` (name -> {dir, group, sid})
    under tmux server `server`. Never forgets the last populated set: with no sessions alive
    (tmux down) the stored set stays as it was."""
    st = _norm(stored)
    if not cur:
        return st
    if server and st["server"] and server != st["server"]:
        # a new tmux server: whatever the old one had is the restore baseline now
        st["baseline"].update({n: v for n, v in st["sessions"].items() if n not in st["baseline"]})
    st["sessions"] = dict(cur)
    for n in cur:                                   # back again: nothing left to restore for it
        st["baseline"].pop(n, None)
    if server:
        st["server"] = server
    return st


def candidates(stored, alive):
    """The agents the Restore button offers: alive before, not alive now, with a conversation."""
    st = _norm(stored)
    pool = dict(st["sessions"])
    pool.update(st["baseline"])
    return {n: v for n, v in sorted(pool.items()) if n not in alive and (v or {}).get("sid")}


def drop(stored, name):
    """An agent closed on purpose through the Office never comes back by itself."""
    st = _norm(stored)
    st["sessions"].pop(name, None)
    st["baseline"].pop(name, None)
    return st


def rename(stored, old, new):
    st = _norm(stored)
    for k in ("sessions", "baseline"):
        if old in st[k]:
            st[k][new] = st[k].pop(old)
    return st


def forget(stored, alive):
    """"Forget these sessions": nothing that is not running now is offered any more."""
    st = _norm(stored)
    st["baseline"] = {}
    st["sessions"] = {n: v for n, v in st["sessions"].items() if n in alive}
    return st
