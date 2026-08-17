#!/bin/bash
# maniMon — panel watchdog.
#
# Author: Manish Jagdish Thatte
#
# The right panel died silently on 17 Aug 2026 and left a stale PID file
# behind; nothing noticed and nothing restarted it. This checks both panels and
# relaunches them if either is gone.
#
# It is intentionally quiet when there is no graphical session: if Manish is
# logged out, panels SHOULD be absent and restarting them is not possible. That
# is not a failure, so it is not logged as one. The recorder is a separate
# service precisely so statistics keep accruing in that state.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$SCRIPT_DIR/watchdog.log"

log() { printf '%s  %s\n' "$(date '+%F %T')" "$1" >> "$LOG"; }

# ── is there a display to draw on? ───────────────────────────────────────────
export DISPLAY="${DISPLAY:-:0}"
: "${XDG_RUNTIME_DIR:=/run/user/$(id -u)}"
if [ -z "$XAUTHORITY" ] || [ ! -r "$XAUTHORITY" ]; then
    for cookie in "$XDG_RUNTIME_DIR"/.mutter-Xwaylandauth.* "$HOME/.Xauthority"; do
        [ -r "$cookie" ] && export XAUTHORITY="$cookie" && break
    done
fi
xprop -root >/dev/null 2>&1 || exit 0     # no session: nothing to do, quietly

# ── are both panels alive? ───────────────────────────────────────────────────
# Checked by process name rather than the PID file alone. A stale PID file is
# exactly the failure seen on 17 Aug, and a PID can also have been recycled by
# an unrelated process — so verify what is actually running.
dead=""
for name in panel_left panel_right; do
    pgrep -f "python3.*${name}\.py" >/dev/null 2>&1 || dead="$dead $name"
done

[ -z "$dead" ] && exit 0

log "panels down:$dead — restarting"
# start.sh stops leftovers first, so a half-dead pair is cleaned up properly.
if "$SCRIPT_DIR/start.sh" >> "$LOG" 2>&1; then
    log "restart ok"
else
    log "restart FAILED (exit $?)"
fi

# Keep the log from growing without bound; it should be readable at a glance.
if [ "$(wc -l < "$LOG" 2>/dev/null || echo 0)" -gt 500 ]; then
    tail -n 200 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi
