#!/bin/bash
# maniMon — install the privileged sensor sampler (needs root).
#
#   sudo bash install_system_sensors.sh
#   sudo bash install_system_sensors.sh --remove
#
# WHY THIS NEEDS ROOT AT ALL, AND WHY IT IS A SEPARATE THING
# ─────────────────────────────────────────────────────────
# Four of the most useful sources on a workstation are root-only: the BMC
# (/dev/ipmi0), NVMe and SATA SMART, and the DIMM inventory from DMI. The
# alternative designs are worse:
#
#   - running the panels as root: a GTK application with a display connection,
#     running as root, forever. No.
#   - sudo inside a 2-second refresh loop: a password prompt or a NOPASSWD rule
#     covering smartctl and ipmitool, which is most of root anyway.
#
# So: one small script, root, on a timer, writing world-readable JSON into a
# tmpfs directory. The panels read files. They never gain privilege, and the
# whole privileged surface is one auditable file you can read in a sitting.
#
# © Manish Jagdish Thatte
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
# The unit sets PYTHONPATH to the repository ROOT — the directory containing
# the `manimon` package — not to this packaging directory.
ROOT="$(cd "$HERE/.." && pwd)"
SRC="$HERE/systemd/system"
UNITS=(manimon-sensors.service manimon-sensors.timer)

if [ "$(id -u)" -ne 0 ]; then
    echo "needs root:  sudo bash $HERE/install_system_sensors.sh ${1:-}"
    exit 1
fi

if [ "${1:-}" = "--remove" ]; then
    systemctl disable --now manimon-sensors.timer 2>/dev/null
    for u in "${UNITS[@]}"; do rm -f "/etc/systemd/system/$u"; done
    systemctl daemon-reload
    echo "removed. BMC, SMART and DIMM data will stop appearing in the panels."
    exit 0
fi

echo "=== checking what is actually readable here ==="
PYTHONPATH="$ROOT" python3 -m manimon sensors --preflight || true

echo
echo "=== installing ==="
for u in "${UNITS[@]}"; do
    sed "s|@INSTALL_DIR@|$ROOT|g" "$SRC/$u" > "/etc/systemd/system/$u" \
        && chmod 644 "/etc/systemd/system/$u" && echo "  $u"
done
systemctl daemon-reload
systemctl enable --now manimon-sensors.timer && echo "  timer enabled + started"

# drivetemp gives SATA/SAS disks a temperature. Without it, most desktops
# report no disk temperature at all and the panel shows a blank where the
# hottest drive should be.
if ! lsmod | grep -q '^drivetemp'; then
    modprobe drivetemp 2>/dev/null && echo "  drivetemp loaded"
    echo drivetemp > /etc/modules-load.d/drivetemp.conf && echo "  drivetemp set to load at boot"
fi

# RAPL energy counters are root-only since CVE-2020-8694 (PLATYPUS), a power
# side-channel on cryptographic code. Making them group-readable is a real, if
# small, tradeoff: it is what allows CPU package watts and therefore whole-
# machine energy accounting. Skip this block if that tradeoff is not one you
# want to make — everything else still works, and the energy figure simply
# becomes GPU-only.
cat > /etc/tmpfiles.d/manimon-rapl.conf <<'EOF'
# Make RAPL energy counters readable so maniMon can report CPU package watts.
# See CVE-2020-8694 before deploying this on a multi-user machine.
z /sys/class/powercap/intel-rapl:0/energy_uj 0444 root root -
z /sys/class/powercap/intel-rapl:0:0/energy_uj 0444 root root -
z /sys/class/powercap/intel-rapl:1/energy_uj 0444 root root -
EOF
systemd-tmpfiles --create /etc/tmpfiles.d/manimon-rapl.conf 2>/dev/null \
    && echo "  RAPL energy counters made readable"

echo
echo "=== verification ==="
systemctl start manimon-sensors.service 2>/dev/null
n="$(ls /run/manimon-sensors/*.json 2>/dev/null | wc -l)"
if [ "$n" -gt 0 ]; then
    printf "  published %s file(s):" "$n"
    for f in /run/manimon-sensors/*.json; do printf ' %s' "$(basename "$f" .json)"; done
    echo
else
    # An active timer proves nothing about published data — verify the files.
    echo "  NO DATA published."
    echo "  check: systemctl status manimon-sensors.service"
    echo "  check: sudo PYTHONPATH=$ROOT python3 -m manimon sensors --once --print"
fi
