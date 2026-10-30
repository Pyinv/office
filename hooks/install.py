#!/usr/bin/env python3
"""Register hooks/ask.py in ~/.claude/settings.json (idempotent; keeps a .bak copy first).
Run once: python3 hooks/install.py"""
import json, os, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
CMD = {"type": "command", "command": os.path.join(HERE, "ask.py"), "timeout": 5}
WANT = {"PreToolUse": "AskUserQuestion", "PermissionRequest": None, "PostToolUse": "AskUserQuestion"}

p = os.path.join(os.path.expanduser("~"), ".claude", "settings.json")
s = json.load(open(p)) if os.path.exists(p) else {}
if os.path.exists(p):
    shutil.copy(p, p + ".bak-office-hook")
hooks = s.setdefault("hooks", {})
for ev, matcher in WANT.items():
    entries = hooks.setdefault(ev, [])
    if any(h.get("command") == CMD["command"] for e in entries for h in e.get("hooks", [])):
        continue
    entries.append({**({"matcher": matcher} if matcher else {}), "hooks": [CMD]})
with open(p, "w") as f:
    json.dump(s, f, indent=2)
    f.write("\n")
print("office hook installed:", ", ".join(WANT))
