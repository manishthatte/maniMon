#!/bin/bash
# maniMon — install the user-scope systemd units.
#
# NO SUDO. These are user units under ~/.config/systemd/user, so nothing needs
# root and nothing is written outside your home directory.
#
#   bash install_user_services.sh            install, enable and start
#   bash install_user_services.sh --status   show what is running
#   bash install_user_services.sh --remove   stop, disable and uninstall
#
# The recorder is deliberately NOT tied to graphical-session.target: the runs
# worth measuring are the long ones that outlive a desktop session, so it keeps
# sampling across logout — provided lingering is enabled for your account.
#
# © Manish Jagdish Thatte
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
# The units set PYTHONPATH to the repository ROOT — the directory that contains
# the `manimon` package — not to this packaging directory.
ROOT="$(cd "$HERE/.." && pwd)"
SRC="$HERE/systemd"
MM=(python3 -m manimon)
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
DEST="$HOME/.config/systemd/user"
UNITS=(manimon-metrics.service manimon-panel@.service)
ENABLE=(manimon-metrics.service manimon-panel@left.service manimon-panel@right.service)

# Units this package used to install and no longer does. Left behind, the
# watchdog timer keeps firing every 60 s against a service file that may not
# even exist any more, and keeps filling the journal — which maniMon reads and
# reports as errors. Removing them is part of installing, not only of --remove.
OBSOLETE=(manimon-watchdog.service manimon-watchdog.timer)

# Earlier versions started the panels from an XDG autostart entry that ran
# start.sh. That script no longer exists, and manimon-panel@.service now owns
# panel startup, so the entry is both broken and redundant: every session start
# and every daemon-reload logs "Exec binary .../start.sh does not exist".
AUTOSTART="$HOME/.config/autostart/thatte-monitor.desktop"

drop_obsolete() {
    for u in "${OBSOLETE[@]}"; do
        [ -e "$DEST/$u" ] || continue
        systemctl --user disable --now "$u" >/dev/null 2>&1
        rm -f "$DEST/$u"
        echo "  removed obsolete $u"
    done
    if [ -e "$AUTOSTART" ] && grep -qs 'start\.sh' "$AUTOSTART"; then
        rm -f "$AUTOSTART"
        echo "  removed obsolete autostart entry (panels are systemd units now)"
    fi
}

case "${1:-install}" in
--status)
    for u in "${ENABLE[@]}"; do
        printf '  %-32s %-10s %s\n' "$u" \
            "$(systemctl --user is-enabled "$u" 2>/dev/null)" \
            "$(systemctl --user is-active "$u" 2>/dev/null)"
    done
    echo
    echo "  linger: $(loginctl show-user "$USER" -p Linger --value 2>/dev/null)"
    "${MM[@]}" info 2>/dev/null | grep -E '"rows"|"size_mb"'
    exit 0
    ;;
--remove)
    for u in "${ENABLE[@]}" "${OBSOLETE[@]}"; do
        systemctl --user disable --now "$u" 2>/dev/null
    done
    for u in "${UNITS[@]}" "${OBSOLETE[@]}"; do rm -f "$DEST/$u"; done
    systemctl --user daemon-reload
    echo "removed. Nothing is recording now — re-run without --remove to restore."
    exit 0
    ;;
esac

mkdir -p "$DEST"
drop_obsolete
for u in "${UNITS[@]}"; do
    # @INSTALL_DIR@ is substituted with wherever this checkout actually lives,
    # so the units work from a git clone, /opt, or anywhere else.
    sed "s|@INSTALL_DIR@|$ROOT|g" "$SRC/$u" > "$DEST/$u" \
        && chmod 644 "$DEST/$u" && echo "  installed $u"
done

systemctl --user daemon-reload

# Lingering is what lets these run with no session. Without it the recorder
# stops at logout, and the overnight runs — the whole reason for keeping
# history — are exactly what goes unrecorded.
if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
    echo
    echo "  WARNING: lingering is OFF for $USER, so these stop at logout."
    echo "  Enable it with:  loginctl enable-linger $USER"
fi

# Panels started the old way — by the watchdog, by an autostart entry, or by
# hand — are not under systemd's control and would sit there as duplicates
# behind the ones the units are about to start. Clear them first.
"${MM[@]}" panels stop >/dev/null 2>&1 || true

echo
for u in "${ENABLE[@]}"; do
    systemctl --user enable --now "$u" >/dev/null 2>&1 \
        && echo "  enabled + started $u" \
        || echo "  ** FAILED to start $u — systemctl --user status $u"
done

echo
echo "=== verification ==="
sleep 3
for u in "${ENABLE[@]}"; do
    printf '  %-32s %s\n' "$u" "$(systemctl --user is-active "$u" 2>/dev/null)"
done
echo
echo "  recorder log:  journalctl --user -u manimon-metrics.service -n 20"
echo "  what is wrong: python3 -m manimon doctor"
echo "  statistics:    python3 -m manimon report"
echo "  per-run costs: python3 -m manimon runs"
echo "  configuration: python3 -m manimon config"
echo
echo "  (from anywhere, once installed:  pip install $ROOT  ->  manimon doctor)"
