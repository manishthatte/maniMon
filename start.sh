#!/bin/bash
# Start maniMon — left (machine) + right (work) panels
# Author: Manish Jagdish Thatte
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE_R="$SCRIPT_DIR/.panel_right.pid"
PIDFILE_L="$SCRIPT_DIR/.panel_left.pid"
LOGFILE_R="$SCRIPT_DIR/panel_right.log"
LOGFILE_L="$SCRIPT_DIR/panel_left.log"
LOCKFILE="$SCRIPT_DIR/.start.lock"

# Prevent concurrent launches (e.g. GNOME autostart firing twice)
if [ -f "$LOCKFILE" ]; then
    age=$(( $(date +%s) - $(stat -c %Y "$LOCKFILE" 2>/dev/null || echo 0) ))
    [ "$age" -lt 10 ] && echo "start.sh already running, skipping." && exit 0
fi
touch "$LOCKFILE"
trap "rm -f $LOCKFILE" EXIT

# ── X display + auth ─────────────────────────────────────────────────────────
# This is a GNOME *Wayland* session; the panels run under XWayland, whose auth
# cookie lives at $XDG_RUNTIME_DIR/.mutter-Xwaylandauth.XXXXXX with a random
# suffix that is regenerated on every login. It must be discovered, never
# hardcoded. Autostart normally inherits both variables, but cron, ssh and a
# manual run do not — so resolve them here rather than assume.
export DISPLAY="${DISPLAY:-:0}"
: "${XDG_RUNTIME_DIR:=/run/user/$(id -u)}"
if [ -z "$XAUTHORITY" ] || [ ! -r "$XAUTHORITY" ]; then
    for cookie in "$XDG_RUNTIME_DIR"/.mutter-Xwaylandauth.* "$HOME/.Xauthority"; do
        if [ -r "$cookie" ]; then export XAUTHORITY="$cookie"; break; fi
    done
fi

# Wait for the display to actually accept connections. Deterministic, unlike a
# fixed autostart delay that is either too short on a cold boot or wasted time
# on a warm one.
for _ in $(seq 1 60); do
    xprop -root >/dev/null 2>&1 && break
    sleep 0.5
done
if ! xprop -root >/dev/null 2>&1; then
    echo "No usable X display (DISPLAY=$DISPLAY XAUTHORITY=$XAUTHORITY) — not starting." >&2
    exit 1
fi

"$SCRIPT_DIR/stop.sh" >/dev/null 2>&1
sleep 1

nohup python3 "$SCRIPT_DIR/panel_right.py" > "$LOGFILE_R" 2>&1 &
echo $! > "$PIDFILE_R"
echo "Right panel (work)    started  PID $(cat "$PIDFILE_R")  log: $LOGFILE_R"

nohup python3 "$SCRIPT_DIR/panel_left.py" > "$LOGFILE_L" 2>&1 &
echo $! > "$PIDFILE_L"
echo "Left  panel (machine) started  PID $(cat "$PIDFILE_L")  log: $LOGFILE_L"
