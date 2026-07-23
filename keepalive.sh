#!/bin/bash
# Office watchdog. Run from cron every minute (+ @reboot). Keeps both servers
# alive on the TAILNET ONLY, so a change/crash/reboot self-heals without a manual
# start. When config changes: kill the process; this brings it back within a minute
# with fresh code. server.py also supports `office/reload` (SIGHUP) for instant reload.
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
DIR="$(cd "$(dirname "$0")" && pwd)"

# Only run on the tailnet — never bind these to the LAN/internet.
IP=$(tailscale ip -4 2>/dev/null | head -1)
[ -z "$IP" ] && exit 0

# 1) web + API + reply server (binds the tailscale IP itself)
if ! pgrep -f "[p]ython3 $DIR/server.py" >/dev/null 2>&1; then
  setsid /usr/bin/python3 "$DIR/server.py" >>/tmp/office-server.log 2>&1 < /dev/null &
fi

# 2) our ttyd terminal server (private unix socket, base-path /terminal). Match OUR
# instance specifically — the packaged ttyd.service (login shell on :7681) must
# not mask it, or the watchdog would never (re)start ours.
if command -v ttyd >/dev/null 2>&1 && ! pgrep -f "[t]tyd .*base-path /terminal" >/dev/null 2>&1; then
  setsid "$DIR/serve-term.sh" >>/tmp/office-term.log 2>&1 < /dev/null &
fi
