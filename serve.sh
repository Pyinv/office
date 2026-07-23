#!/bin/bash
# Serve ~/office (Claude status dashboard + design docs) on the tailnet ONLY.
# Never bind 0.0.0.0: this content contains prompt text and private data
# that must not leave the tailnet. Waits for the tailscale IP before binding.
# server.py binds the tailscale IP itself and refuses to start without one, so
# the reply endpoint (which types into live Claude sessions) is never exposed
# off-tailnet.
exec /usr/bin/python3 "$(dirname "$0")/server.py"
