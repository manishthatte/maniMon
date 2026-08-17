#!/bin/bash
# Stop maniMon
# Author: Manish Jagdish Thatte
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

for pf in "$SCRIPT_DIR/.panel_right.pid" "$SCRIPT_DIR/.panel_left.pid" "$SCRIPT_DIR/.sidebar.pid"; do
    if [ -f "$pf" ]; then
        pid=$(cat "$pf" 2>/dev/null)
        [ -n "$pid" ] && kill "$pid" 2>/dev/null && echo "Stopped PID $pid"
        rm -f "$pf"
    fi
done
# Current names, plus panel.py / sidebar.py — superseded on 14 Aug 2026 and
# deleted on 18 Aug. The pkill lines stay: the files are gone, but a process
# started from an older checkout could still be holding a strut, and killing a
# name that no longer exists costs nothing.
pkill -f "python3.*panel_right\.py" 2>/dev/null
pkill -f "python3.*panel_left\.py"  2>/dev/null
pkill -f "python3.*panel\.py"       2>/dev/null
pkill -f "python3.*sidebar\.py"     2>/dev/null
echo "System Monitor stopped"
