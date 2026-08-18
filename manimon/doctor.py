"""
`manimon doctor` — check the installation and say what is missing.

Every section a panel can show depends on something: a config key, a published
sensor file, a binary, a kernel interface. When a section is empty the question
is always *which* of those is absent, and answering it used to mean reading
source. This answers it in one command, and every finding carries the exact
command that fixes it.

The rule the rest of this program is written to applies here too: report what
was actually observed. A check that could not run says so, and is never quietly
folded into a pass.
"""

import os
import shutil
import subprocess
import time

from .util import which

OK, WARN, FAIL, SKIP = 'ok', 'warn', 'fail', 'skip'

MARK = {OK: '  ok  ', WARN: ' warn ', FAIL: ' FAIL ', SKIP: ' --   '}


class Report:
    def __init__(self, verbose=False):
        self.rows = []
        self.verbose = verbose

    def add(self, status, area, detail, fix=None):
        self.rows.append((status, area, detail, fix))

    def counts(self):
        c = {OK: 0, WARN: 0, FAIL: 0, SKIP: 0}
        for s, *_ in self.rows:
            c[s] += 1
        return c

    def render(self):
        width = max((len(a) for _, a, _, _ in self.rows), default=10)
        shown = 0
        for status, area, detail, fix in self.rows:
            if status == OK and not self.verbose:
                continue
            shown += 1
            print(f"[{MARK[status]}] {area:<{width}}  {detail}")
            if fix:
                for line in fix.splitlines():
                    print(f"{'':>{width + 10}}{line}")
        c = self.counts()
        if not shown:
            print("Everything checked is working. `-v` shows the passing checks too.")
            print()
        else:
            print()
        print(f"{c[OK]} ok · {c[WARN]} warning · {c[FAIL]} failed · {c[SKIP]} not applicable")
        return 1 if c[FAIL] else 0


def _age(seconds):
    if seconds < 90:
        return f"{seconds:.0f} s ago"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min ago"
    if seconds < 172800:
        return f"{seconds / 3600:.1f} h ago"
    return f"{seconds / 86400:.1f} days ago"


def _unit_active(unit, user=True):
    cmd = ['systemctl'] + (['--user'] if user else []) + ['is-active', unit]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return out.stdout.strip()
    except Exception:
        return 'unknown'


# ── the checks ───────────────────────────────────────────────────────────────
def check_python(r):
    import sys
    from . import __version__
    v = '.'.join(str(x) for x in sys.version_info[:3])
    r.add(OK, 'version', f"maniMon {__version__} on Python {v}")


def check_config(r):
    from . import config
    src = config.CFG.get('_sources') or []
    if src:
        r.add(OK, 'config', f"loaded from {', '.join(src)}")
    else:
        r.add(WARN, 'config', "no config file found — running on built-in defaults",
              "manimon config --sample > ~/.config/manimon/config.toml")


def check_state_dir(r):
    from .config import STATE_DIR
    if not os.path.isdir(STATE_DIR):
        r.add(FAIL, 'state dir', f"{STATE_DIR} does not exist",
              f"mkdir -p {STATE_DIR}")
        return
    if not os.access(STATE_DIR, os.W_OK):
        r.add(FAIL, 'state dir', f"{STATE_DIR} is not writable")
        return
    free = shutil.disk_usage(STATE_DIR).free / 1e9
    if free < 1.0:
        r.add(WARN, 'state dir', f"{STATE_DIR} — only {free:.1f} GB free")
    else:
        r.add(OK, 'state dir', f"{STATE_DIR} — {free:.0f} GB free")


def check_store(r):
    from .store.metrics import Reader, DB_PATH
    with Reader() as reader:
        info = reader.info()
    if not info.get('available'):
        r.add(WARN, 'metric store', f"no store yet at {DB_PATH}",
              "It appears once the recorder has run:\n"
              "  systemctl --user start manimon-metrics.service")
        return
    raw = info.get('r', {})
    rows, hi = raw.get('rows', 0), raw.get('to')
    span = ''
    if raw.get('from') and hi:
        span = f", {(hi - raw['from']) / 3600:.1f} h span"
    if not hi:
        r.add(FAIL, 'metric store', f"{info['size_mb']} MB but no raw samples",
              "The recorder is not writing. Check:\n"
              "  systemctl --user status manimon-metrics.service")
        return
    age = time.time() - hi
    detail = f"{rows} raw rows{span}, {info['size_mb']} MB, newest {_age(age)}"
    # 10 s sampling: a minute of silence is a stopped recorder, not jitter.
    if age > 300:
        r.add(FAIL, 'metric store', detail,
              "Nothing has been recorded recently:\n"
              "  systemctl --user status manimon-metrics.service")
    elif age > 60:
        r.add(WARN, 'metric store', detail)
    else:
        r.add(OK, 'metric store', detail)


def check_recorder(r):
    state = _unit_active('manimon-metrics.service')
    if state == 'active':
        r.add(OK, 'recorder', 'manimon-metrics.service active')
    elif state == 'unknown':
        r.add(SKIP, 'recorder', 'systemctl unavailable')
    else:
        r.add(WARN, 'recorder', f"manimon-metrics.service is {state}",
              "bash packaging/install_user_services.sh")


def check_sensors(r):
    from .config import SENSOR_DIR
    from .sensors.daemon import SAMPLERS, PERIODS
    if not os.path.isdir(SENSOR_DIR):
        r.add(WARN, 'sensor daemon', f"{SENSOR_DIR} does not exist — nothing published",
              "sudo bash packaging/install_system_sensors.sh")
        return
    missing, stale, fresh = [], [], []
    for name in SAMPLERS:
        path = f"{SENSOR_DIR}/{name}.json"
        if not os.path.exists(path):
            missing.append(name)
            continue
        age = time.time() - os.path.getmtime(path)
        # Judge each source against its OWN declared period, not one invented
        # threshold. `dimms` has period None — it is sampled once, because DMI
        # does not change while the machine is up, so it can never be stale.
        period = PERIODS.get(name)
        overdue = period is not None and age > period * 3
        (stale if overdue else fresh).append(f"{name}({_age(age)})")
    if fresh:
        r.add(OK, 'sensor daemon', f"publishing: {', '.join(fresh)}")
    if stale:
        r.add(WARN, 'sensor daemon', f"stale: {', '.join(stale)}",
              "sudo systemctl start manimon-sensors.service")
    if missing:
        # Not a failure. A desktop board has no BMC and never will.
        r.add(WARN, 'sensor daemon', f"never published: {', '.join(missing)}",
              "Expected if the hardware is absent. To check:\n"
              "  sudo manimon sensors --once --print")


def check_binaries(r):
    # (binary, what it unlocks, hard requirement?)
    for exe, gives, hard in (('smartctl', 'drive health and lifetime writes', False),
                             ('ipmitool', 'chassis fans, board rails, DIMM power', False),
                             ('dmidecode', 'memory channel population', False),
                             ('nvme', 'NVMe SMART log', False),
                             ('xprop', 'display readiness check before the panels start', True),
                             ('pgrep', 'finding panels this launcher did not start', True)):
        # which() searches sbin too. Looking only at PATH reports the
        # privileged tools missing on precisely the machines that have them.
        path = which(exe)
        if path:
            r.add(OK, f'bin:{exe}', f"{path} — {gives}")
        else:
            r.add(FAIL if hard else WARN, f'bin:{exe}', f"not installed — no {gives}")


def check_kernel_interfaces(r):
    import glob
    for label, pattern, gives in (
            ('hwmon',  '/sys/class/hwmon/hwmon*',            'CPU and board temperatures'),
            ('amdgpu', '/sys/class/drm/card*/device/gpu_busy_percent', 'GPU utilisation'),
            ('rapl',   '/sys/class/powercap/intel-rapl:0',   'CPU package power'),
            ('edac',   '/sys/devices/system/edac/mc/mc*',    'ECC error counters')):
        hits = glob.glob(pattern)
        if hits:
            r.add(OK, label, f"{len(hits)} present — {gives}")
        else:
            r.add(SKIP, label, f"absent — no {gives}")
    if os.path.exists('/proc/pressure/cpu'):
        r.add(OK, 'psi', 'pressure stall information available')
    else:
        r.add(SKIP, 'psi', 'kernel built without CONFIG_PSI')


def check_gtk(r):
    try:
        import gi
        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk                     # noqa: F401
        r.add(OK, 'gtk', 'PyGObject and GTK 3 importable')
    except Exception as e:
        r.add(WARN, 'gtk', f"panels unavailable — {type(e).__name__}: {e}",
              "Debian/Ubuntu:  sudo apt install python3-gi gir1.2-gtk-3.0\n"
              "Fedora:         sudo dnf install python3-gobject gtk3")


def check_panels(r):
    """Where a panel is supervised matters as much as whether it is running.

    A panel running under manimon-panel@<side>.service comes back by itself.
    The same panel started by hand does not, and looks identical in ps — so
    say which it is rather than only that something is alive.
    """
    from .ui.launcher import PANELS, _read_pid, _find_running
    for name in PANELS:
        unit = f'manimon-panel@{name}.service'
        state = _unit_active(unit) if which('systemctl') else None
        pid = _read_pid(name) or _find_running(name)
        if state == 'active':
            r.add(OK, f'panel:{name}', f"{unit} active — restarts on its own")
        elif pid:
            r.add(WARN, f'panel:{name}', f"running as PID {pid}, unsupervised",
                  "Nothing will restart it if it dies. Put it under systemd:\n"
                  "  bash packaging/install_user_services.sh")
        else:
            r.add(WARN, f'panel:{name}', 'not running',
                  f"systemctl --user start {unit}\n"
                  "or, without systemd:  manimon panels start")


def check_display(r):
    if os.environ.get('WAYLAND_DISPLAY') and not os.environ.get('DISPLAY'):
        r.add(WARN, 'display', 'Wayland session with no DISPLAY — panels need XWayland')
    elif os.environ.get('DISPLAY'):
        r.add(OK, 'display', f"DISPLAY={os.environ['DISPLAY']}")
    else:
        r.add(SKIP, 'display', 'headless — CLI works, panels will not start')


def check_readers(r):
    """Run one collection pass and report any reader that threw.

    doctor once said "21 ok, 0 failed" on an installation where a reader could
    have been failing on every tick without anything showing it. Checking that
    the sensors exist is not the same as checking that reading them works, and
    only the second one is what the panel actually does.
    """
    from .collect import Collector
    c = Collector()
    c.tick(force_all=True)
    errors = c.snap.get('_errors') or {}
    if not errors:
        r.add(OK, 'readers', f"{len(c.snap)} sections collected, none failing")
        return
    for key, msg in sorted(errors.items()):
        r.add(FAIL, f'reader:{key}', msg,
              "The panel is showing a default for this section.\n"
              f"Reproduce with:  python3 -m manimon.collect --dump {key}")


def check_backup_logs(r):
    """Backup freshness is read from a log, so the log has to be the real one.

    The panel reported a backup as 3.8 days old and 12m16s long while it had
    finished twenty minutes earlier in 22 s: the configured path was one no
    script wrote any more. A path that exists but is never updated is worse
    than one that is missing, because it reports a plausible wrong answer
    instead of nothing.
    """
    from .config import BACKUP_DIR, BACKUP_JOBS
    if not BACKUP_JOBS:
        r.add(SKIP, 'backup logs', 'no [backups] jobs configured')
        return
    for logname, label, stale_days in BACKUP_JOBS:
        path = os.path.join(BACKUP_DIR or '', logname)
        area = f'backup:{label}'
        if not os.path.exists(path):
            r.add(WARN, area, f"{path} does not exist",
                  "Nothing has written this log. Either the backup has never\n"
                  "run, or it writes somewhere else and [backups].jobs is\n"
                  "pointing at the wrong filename.")
            continue
        age = time.time() - os.path.getmtime(path)
        if age > stale_days * 86400:
            r.add(WARN, area, f"last run {_age(age)} — older than {stale_days} d")
        else:
            r.add(OK, area, f"last run {_age(age)}")


def check_obsolete_units(r):
    """Units this package used to install and no longer does.

    A left-behind manimon-watchdog.timer keeps firing every 60 s, and with the
    panels in its cgroup systemd logs four complaints per firing — roughly
    5,700 journal lines a day, which maniMon then reads back and reports as
    errors needing attention.
    """
    if not which('systemctl'):
        r.add(SKIP, 'obsolete units', 'systemctl unavailable')
        return
    home = os.path.expanduser('~/.config/systemd/user')
    stale = [u for u in ('manimon-watchdog.service', 'manimon-watchdog.timer')
             if os.path.exists(os.path.join(home, u))]
    autostart = os.path.expanduser('~/.config/autostart/thatte-monitor.desktop')
    if os.path.exists(autostart):
        stale.append('thatte-monitor.desktop (autostart)')
    if stale:
        r.add(WARN, 'obsolete units', ', '.join(stale),
              "Superseded by manimon-panel@.service. Remove them with:\n"
              "  bash packaging/install_user_services.sh")
    else:
        r.add(OK, 'obsolete units', 'none left behind')


CHECKS = (check_python, check_config, check_state_dir, check_store, check_recorder,
          check_sensors, check_binaries, check_kernel_interfaces, check_gtk,
          check_display, check_panels, check_readers, check_backup_logs,
          check_obsolete_units)


def run(verbose=False):
    r = Report(verbose=verbose)
    for fn in CHECKS:
        try:
            fn(r)
        except Exception as e:
            r.add(FAIL, fn.__name__.replace('check_', ''),
                  f"the check itself failed — {type(e).__name__}: {e}")
    return r.render()
