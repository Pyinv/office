#!/bin/bash
# Refresh ~/.claude/last-usage.json so the /claude/status dashboard shows current
# rate-limit bars even when no Claude session is running.
#
# Skips the probe if last-usage.json was updated in the last SKIP_IF_FRESH_S seconds
# (so we don't fire during an active session — the statusline already ticks it).
# Uses haiku to minimise both dollar and 5-hour-quota impact.

STAMP="$HOME/.claude/last-usage.json"
SKIP_IF_FRESH_S=${SKIP_IF_FRESH_S:-900}   # 15 min
LOG=/tmp/claude-status-probe.log
CLAUDE_BIN="${OFFICE_CLAUDE_BIN:-$(command -v claude || echo "$HOME/.local/bin/claude")}"

if [ -f "$STAMP" ]; then
  age=$(( $(date +%s) - $(stat -c %Y "$STAMP") ))
  if [ "$age" -lt "$SKIP_IF_FRESH_S" ]; then
    echo "$(date -Is) skip: last-usage.json is ${age}s fresh" >> "$LOG"
    exit 0
  fi
fi

echo "$(date -Is) probing…" >> "$LOG"
# Use a bare-minimum prompt with haiku; run headless.
result=$(timeout 30 "$CLAUDE_BIN" -p --model haiku "ok" --output-format json 2>&1)
rc=$?
cost=$(echo "$result" | python3 -c 'import json,sys;
try: d=json.load(sys.stdin); print(f"cost=${d.get(\"total_cost_usd\",0):.4f} dur={d.get(\"duration_ms\",0)}ms")
except Exception as e: print("parse err:", e)' 2>/dev/null)
echo "$(date -Is) done rc=$rc $cost" >> "$LOG"
