"""
Small shared helpers: file reads that cannot raise, one subprocess wrapper,
and the human-readable formatters.

These existed in three separate copies before — collectors, the panel base and
health.py each carried their own rf/ri/sh. One copy, one behaviour.
"""

import os, glob, shutil, subprocess

# Privileged tools live in sbin, which is not on an ordinary user's PATH.
# shutil.which alone therefore reports smartctl, nvme and dmidecode missing on
# exactly the machines that have them — and hardcoding /usr/sbin, as this code
# used to, breaks on any distribution that has not merged /sbin.
SBIN_DIRS = ('/usr/sbin', '/sbin', '/usr/local/sbin')


def which(name, extra_dirs=SBIN_DIRS):
    """Locate an executable, searching PATH and then the sbin directories."""
    hit = shutil.which(name)
    if hit:
        return hit
    for d in extra_dirs:
        p = os.path.join(d, name)
        if os.access(p, os.X_OK):
            return p
    return None


HZ   = os.sysconf('SC_CLK_TCK') or 100
PAGE = os.sysconf('SC_PAGE_SIZE') or 4096


# JOB_BINS, MPI_LAUNCHERS and BACKUP_JOBS all come from config.py — see the
# import block above. They are configuration, not facts about the code.


# ── Low-level helpers (shared with panel_common) ──────────────────────────────
def rf(path, default=""):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except Exception:
        return default


def ri(path, default=0):
    try:
        return int(rf(path, str(default)))
    except Exception:
        return default


def sh(cmd, timeout=2):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True,
                              text=True, timeout=timeout).stdout.strip()
    except Exception:
        return ""


def fmt_rate(b):
    if b >= 1_073_741_824: return f"{b/1_073_741_824:.1f}GB/s"
    if b >= 1_048_576:     return f"{b/1_048_576:.1f}MB/s"
    if b >= 1_024:         return f"{b/1_024:.0f}KB/s"
    return f"{b:.0f}B/s"


def fmt_bytes(b):
    if b >= 1_099_511_627_776: return f"{b/1_099_511_627_776:.1f}T"
    if b >= 1_073_741_824:     return f"{b/1_073_741_824:.1f}G"
    if b >= 1_048_576:         return f"{b/1_048_576:.0f}M"
    if b >= 1_024:             return f"{b/1_024:.0f}K"
    return f"{b:.0f}B"


def fmt_elapsed(s):
    s = int(s)
    if s < 60:    return f"{s}s"
    if s < 3600:  return f"{s//60}m{s%60:02d}s"
    if s < 86400: return f"{s//3600}h{(s%3600)//60:02d}m"
    return f"{s//86400}d{(s%86400)//3600:02d}h"


def fmt_age(seconds):
    """Human 'how long ago' — coarser than fmt_elapsed."""
    s = int(seconds)
    if s < 90:     return f"{s}s ago"
    if s < 5400:   return f"{s//60}m ago"
    if s < 172800: return f"{s//3600}h ago"
    return f"{s//86400}d ago"


def hwmon_by_name(name):
    """All hwmon directories whose 'name' file matches."""
    out = []
    for h in sorted(glob.glob('/sys/class/hwmon/hwmon*')):
        if rf(f'{h}/name') == name:
            out.append(h)
    return out


def read_temps(hw):
    """{label: celsius} for one hwmon directory."""
    res = {}
    for tin in sorted(glob.glob(f'{hw}/temp*_input')):
        idx = os.path.basename(tin).replace('temp', '').replace('_input', '')
        lbl = rf(f'{hw}/temp{idx}_label') or f'temp{idx}'
        val = ri(tin, 0)
        if val:
            res[lbl] = round(val / 1000.0, 1)
    return res
