#!/bin/bash
# Called by ttyd with the tmux session name as $1 (from the ?arg= query param).
# Validate it's a real session before attaching so a bogus arg can do nothing.
s="$1"
if [ -n "$s" ] && tmux has-session -t "$s" 2>/dev/null; then
  # attach read-write to the live session. Only client in the normal workflow
  # (you're not ssh-attached), so tmux sizes the window to the browser.
  # Title the browser tab after the session: tmux emits the name as the terminal
  # title (OSC), ttyd mirrors that into document.title. Session-scoped, so it does
  # not touch the global tmux config.
  tmux set-option -t "$s" set-titles on 2>/dev/null
  title="$s"
  if [[ "$s" == *-* ]]; then       # "alex-web-app" -> "Web App | Alex" (same format as the in-page terminal)
    person="${s%%-*}"; proj="${s#*-}"
    proj="$(echo "$proj" | tr -- '-_' '  ' | sed -E 's/\b(.)/\u\1/g')"
    title="$proj | ${person^}"
  fi
  tmux set-option -t "$s" set-titles-string "$title" 2>/dev/null
  exec tmux attach -t "$s"
fi
echo "no such tmux session: '$s'"
sleep 2
