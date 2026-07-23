#!/bin/bash
# ttyd terminal server for the Office. Listens on a private UNIX SOCKET (no TCP port),
# attaches to a tmux session named by ?arg=<session>, writable so you can type. The
# office reaches it only through the server.py reverse proxy at /terminal; a browser
# cannot open a websocket to a unix socket, so no foreign page can reach the shell.
#
# One-time install:  sudo apt install -y ttyd
if ! command -v ttyd >/dev/null 2>&1; then
  echo "ttyd not installed. Run:  sudo apt install -y ttyd" >&2
  exit 1
fi
DIR="$(cd "$(dirname "$0")" && pwd)"
# Bind a UNIX DOMAIN SOCKET, not a TCP port. A writable terminal on a TCP loopback
# port is still reachable by any webpage in your browser (ttyd's --check-origin does
# not reliably block that cross-origin shell-websocket). A unix socket has no port for
# a browser to reach at all, so that entire attack class is impossible. Only server.py
# dials this socket; base-path lets it forward /terminal/* transparently.
rm -f "$DIR/ttyd.sock"          # clear any stale socket left by a previous crash
exec ttyd --writable --url-arg -i "$DIR/ttyd.sock" --base-path /terminal \
  -t fontSize=14 -t 'theme={"background":"#0c0f14","foreground":"#e8eef6"}' \
  -t disableLeaveAlert=true -t reconnect=2 \
  "$DIR/attach.sh"
