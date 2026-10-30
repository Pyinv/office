#!/usr/bin/env python3
"""Claude Code hook: keep what an agent is asking you, as Claude Code hands it over, for the Office.

PreToolUse(AskUserQuestion) and PermissionRequest write the event's JSON (tool name and input) to
~/.claude/office-asks/<session id>.json; PostToolUse / PostToolUseFailure of that tool remove it.
The Office shows the file only while the agent's own status file says it is waiting. Never blocks
an agent: no output, always exit 0."""
import json, os, sys

def main():
    try:
        e = json.load(sys.stdin)
        sid = str(e.get("session_id") or "")
        if not sid or "/" in sid:
            return
        d = os.path.join(os.path.expanduser("~"), ".claude", "office-asks")
        path = os.path.join(d, sid + ".json")
        ev = e.get("hook_event_name")
        if ev in ("PreToolUse", "PermissionRequest"):
            os.makedirs(d, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"event": ev, "tool_name": e.get("tool_name", ""),
                           "tool_input": e.get("tool_input") or {}}, f, ensure_ascii=False)
            os.replace(tmp, path)
        elif ev in ("PostToolUse", "PostToolUseFailure", "PermissionDenied"):
            os.remove(path)
    except Exception:
        pass

main()
