"""
Git repository state, backup freshness, systemd units, the journal and host facts.
"""

import os, re, json, time, socket

from ..util import rf, sh
from ..config import REPO_PATH, BACKUP_DIR, BACKUP_JOBS, SERVICES, VENVS
from .process import _tail


# ═══════════════════════════════════════════════════════════════════════════════
#  Repo, backups, services, journal
# ═══════════════════════════════════════════════════════════════════════════════
def repo():
    if not REPO_PATH:                        # no repo configured: hide the section
        return {}
    g = f'git -C {REPO_PATH}'
    branch = sh(f'{g} branch --show-current', timeout=3) or '?'
    dirty = sh(f'{g} status --porcelain', timeout=5)
    ndirty = len([l for l in dirty.split('\n') if l.strip()])
    unpushed = sh(f'{g} rev-list --count @{{u}}..HEAD 2>/dev/null', timeout=3)
    # NOTE: the format string must be quoted — an unquoted %h|%cr|%s is parsed
    # as a shell pipeline and silently yields nothing.
    last = sh(f"{g} log -1 --format='%h\x1f%cr\x1f%s'", timeout=3)
    parts = last.split('\x1f', 2)
    return {
        'branch': branch,
        'dirty': ndirty,
        'unpushed': int(unpushed) if unpushed.isdigit() else 0,
        'last_hash': parts[0] if parts else '',
        'last_when': parts[1] if len(parts) > 1 else '',
        'last_subject': parts[2][:44] if len(parts) > 2 else '',
    }


def backups():
    """Last run of each backup job, from its log file, plus drive mount state."""
    out = []
    mounted = {}
    for line in rf('/proc/mounts').split('\n'):
        p = line.split()
        if len(p) >= 2 and p[1].startswith('/media/'):
            mounted[os.path.basename(p[1])] = p[1]

    for logname, label, stale_days in BACKUP_JOBS:
        path = os.path.join(BACKUP_DIR or '', logname)
        job = {'name': logname.replace('backup_', '').replace('.log', ''),
               'label': label, 'mounted': label in mounted,
               'age': None, 'ok': None, 'duration': '', 'stale_days': stale_days}
        if os.path.exists(path):
            job['age'] = time.time() - os.path.getmtime(path)
            tail = _tail(path, 4096)
            for ln in reversed(tail):
                m = re.search(r'DONE in ([\dhms ]+)', ln)
                if m:
                    job['duration'] = m.group(1).strip()
                    job['ok'] = True
                    break
                if re.search(r'\b(FAILED|ERROR)\b', ln, re.I):
                    job['ok'] = False
                    break
        job['stale'] = job['age'] is not None and job['age'] > stale_days * 86400
        out.append(job)
    return out


def services(names=SERVICES):
    """
    One subprocess for all units, not one per unit.

    Reports 'absent' for a unit that is not installed, distinct from
    'inactive' for one that is installed and stopped. `is-active` collapses
    both to "inactive", which would render a never-installed service as a grey
    dot indistinguishable from one that had died.
    """
    res = {}
    for scope in ('system', 'user'):
        units = [u for _, u, s in names if s == scope]
        if not units:
            continue
        flag = '--user ' if scope == 'user' else ''
        out = sh(f'systemctl {flag}show --property=Id --property=LoadState '
                 f'--property=ActiveState {" ".join(units)} 2>/dev/null', timeout=4)
        parsed, cur = {}, {}
        for line in out.split('\n'):
            line = line.strip()
            if not line:
                if cur.get('Id'):
                    parsed[cur['Id']] = cur
                cur = {}
                continue
            if '=' in line:
                k, v = line.split('=', 1)
                cur[k] = v
        if cur.get('Id'):
            parsed[cur['Id']] = cur

        for label, unit, s in names:
            if s != scope:
                continue
            rec = parsed.get(unit)
            if not rec:
                res[label] = 'unknown'
            elif rec.get('LoadState') in ('not-found', 'masked'):
                res[label] = 'absent'
            else:
                res[label] = rec.get('ActiveState', 'unknown')
    return res


def journal():
    err = sh('journalctl -b -p 3 --no-pager -q 2>/dev/null | wc -l', timeout=4)
    warn = sh('journalctl -b -p 4..4 --no-pager -q 2>/dev/null | wc -l', timeout=4)
    recent = []
    for line in sh('journalctl -b -p 4 -n 3 --no-pager -q -o short 2>/dev/null',
                   timeout=4).split('\n'):
        if line.strip():
            p = line.split(None, 4)
            recent.append(p[4][:52] if len(p) >= 5 else line[:52])
    return {
        'errors': int(err) if err.isdigit() else 0,
        'warnings': int(warn) if warn.isdigit() else 0,
        'recent': recent,
    }


def taskbar_reserved(side='RIGHT'):
    """
    Pixels already reserved on a screen edge by the desktop's own taskbar.

    A GNOME Shell panel is drawn by the shell itself, so it never appears in
    _NET_CLIENT_LIST and cannot be discovered by walking X windows — it has to
    be asked for by name. dash-to-panel is checked first, then dash-to-dock.
    Returns 0 when nothing occupies that edge.
    """
    side = side.upper()
    try:
        pos = sh('gsettings get org.gnome.shell.extensions.dash-to-panel '
                 'panel-positions', 2).strip().strip("'")
        size = sh('gsettings get org.gnome.shell.extensions.dash-to-panel '
                  'panel-sizes', 2).strip().strip("'")
        if pos and size:
            p, s = json.loads(pos), json.loads(size)
            for mon, where in p.items():
                if str(where).upper() == side:
                    return int(s.get(mon, 0))
    except Exception:
        pass
    try:
        dpos = sh('gsettings get org.gnome.shell.extensions.dash-to-dock '
                  'dock-position', 2).strip().strip("'")
        if dpos.upper() == side:
            fixed = sh('gsettings get org.gnome.shell.extensions.dash-to-dock '
                       'dock-fixed', 2).strip()
            if fixed == 'true':
                icon = sh('gsettings get org.gnome.shell.extensions.dash-to-dock '
                          'dash-max-icon-size', 2).strip()
                return int(icon) + 26 if icon.isdigit() else 64
    except Exception:
        pass
    return 0


def sysinfo():
    """
    Version and identity strings for the footer.

    ROCm gets a real answer rather than '?'. After the 17 Aug reinstall the
    userspace runtime is gone while the kernel driver is still loaded, so the
    GPU works as a display adapter and not as a compute device — that is a
    current, work-blocking fact, and a question mark does not say it.
    """
    up = float(rf('/proc/uptime', '0 0').split()[0])
    rocm = (rf('/opt/rocm/.info/version') or
            sh('ls -d /opt/rocm-* 2>/dev/null | head -1 | sed "s|.*rocm-||"', 1))
    kfd = os.path.exists('/dev/kfd')
    if not rocm:
        rocm_state = 'not installed' if kfd else 'absent'
    else:
        rocm_state = rocm.strip()[:16]
    venv = ''
    for p in VENVS:
        if os.path.exists(f'{p}/bin/python'):
            venv = os.path.basename(p)
            break
    return {
        'host': socket.gethostname(),
        'uptime': up,
        'kernel': os.uname().release,
        'rocm': rocm_state,
        'rocm_ok': bool(rocm),
        # The driver being loaded while the runtime is missing is exactly the
        # current state, and the panel should be able to say so.
        'kfd': kfd,
        # The in-tree amdgpu exposes no `version` attribute at all — reading it
        # and defaulting to a string reported "not loaded" for a driver that is
        # plainly working. Presence of the module directory is the real test;
        # its version is the kernel's.
        'amdgpu': ('in-tree' if os.path.isdir('/sys/module/amdgpu')
                   else 'not loaded'),
        'venv': venv,
    }
