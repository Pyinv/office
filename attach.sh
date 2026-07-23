#!/bin/bash
# Called by ttyd with the tmux session name as $1 (from the ?arg= query param).
# Validate it's a real session before attaching so a bogus arg can do nothing.
s="$1"
if [ -n "$s" ] && tmux has-session -t "$s" 2>/dev/null; then
  # attach read-write to the live session. Only client in the normal workflow
  # (you're not ssh-attached), so tmux sizes the window to the browser.
  exec tmux attach -t "$s"
fi
echo "no such tmux session: '$s'"
sleep 2
