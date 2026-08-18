"""
Starting and stopping the docked panels.

This was two shell scripts. It is Python now for one reason that matters: the
display discovery below is the fiddly part, and having it in the same language
as everything else means it can be tested and reported on rather than only
echoed about.

Pidfiles and logs live in the state directory, not in the source tree. A
program that writes into its own checkout cannot be installed read-only, and
`git status` should never be dirtied by running the thing.
"""

import os
import signal
import subprocess
import sys
import time

from ..config import STATE_DIR

PANELS = {
    'left':  ('manimon.ui.left',  'machine'),
    'right': ('manimon.ui.right', 'work'),
}


def _paths(name):
    return (os.path.join(STATE_DIR, f'{name}.pid'),
            os.path.join(STATE_DIR, f'{name}.log'))


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, TypeError):
        return False


def _read_pid(name):
    pf, _ = _paths(name)
    try:
        pid = int(open(pf).read().strip())
    except (OSError, ValueError):
        return None
    return pid if _alive(pid) else None


def _find_running(name):
    """PID of a panel started by some other means — an old checkout, a manual run."""
    mod = PANELS[name][0]
    out = subprocess.run(['pgrep', '-f', mod], capture_output=True, text=True).stdout
    pids = [int(x) for x in out.split() if x.isdigit() and int(x) != os.getpid()]
    return pids[0] if pids else None


# ── X display ────────────────────────────────────────────────────────────────
# On a GNOME *Wayland* session the panels run under XWayland, whose auth cookie
# is at $XDG_RUNTIME_DIR/.mutter-Xwaylandauth.XXXXXX with a random suffix
# regenerated at every login. It must be discovered, never hardcoded. An
# autostart entry inherits DISPLAY and XAUTHORITY; cron, ssh and a manual run
# do not — so resolve them here rather than assume.
def _resolve_display(env):
    env.setdefault('DISPLAY', ':0')
    env.setdefault('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
    xauth = env.get('XAUTHORITY')
    if xauth and os.access(xauth, os.R_OK):
        return env
    import glob
    for cookie in (sorted(glob.glob(f"{env['XDG_RUNTIME_DIR']}/.mutter-Xwaylandauth.*"))
                   + [os.path.expanduser('~/.Xauthority')]):
        if os.access(cookie, os.R_OK):
            env['XAUTHORITY'] = cookie
            break
    return env


def _display_ready(env, wait=30.0):
    """Wait until the server actually accepts a connection.

    Deterministic, unlike a fixed autostart delay that is either too short on a
    cold boot or wasted time on a warm one.
    """
    deadline = time.monotonic() + wait
    while True:
        if subprocess.run(['xprop', '-root'], env=env, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)


# ── actions ──────────────────────────────────────────────────────────────────
def _wanted(side):
    return list(PANELS) if side == 'both' else [side]


def start(side='both', quiet=False):
    os.makedirs(STATE_DIR, exist_ok=True)
    env = _resolve_display(dict(os.environ))
    if not _display_ready(env):
        print(f"No usable X display (DISPLAY={env.get('DISPLAY')} "
              f"XAUTHORITY={env.get('XAUTHORITY')}) — not starting.", file=sys.stderr)
        return 1

    stop(side, quiet=True)
    rc = 0
    for name in _wanted(side):
        mod, role = PANELS[name]
        pf, lf = _paths(name)
        with open(lf, 'ab', buffering=0) as log:
            proc = subprocess.Popen([sys.executable, '-m', mod], env=env,
                                    stdout=log, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
        open(pf, 'w').write(str(proc.pid))
        # A panel that dies on import leaves a pidfile pointing at a corpse,
        # which reads as "started" to anything that only checks the file.
        time.sleep(0.4)
        if proc.poll() is not None:
            print(f"{name} panel exited immediately (rc={proc.returncode}); see {lf}",
                  file=sys.stderr)
            rc = 1
        elif not quiet:
            print(f"{name:<5} panel ({role:<7}) started  PID {proc.pid}  log: {lf}")
    return rc


def stop(side='both', quiet=False):
    for name in _wanted(side):
        pf, _ = _paths(name)
        pid = _read_pid(name) or _find_running(name)
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
                if not quiet:
                    print(f"{name:<5} panel stopped (PID {pid})")
            except OSError as e:
                print(f"{name}: could not stop PID {pid} — {e}", file=sys.stderr)
        elif not quiet:
            print(f"{name:<5} panel not running")
        try:
            os.unlink(pf)
        except OSError:
            pass
    return 0


def status(side='both', quiet=False):
    running = 0
    for name in _wanted(side):
        _, lf = _paths(name)
        pid = _read_pid(name)
        stray = None if pid else _find_running(name)
        if pid:
            running += 1
            print(f"{name:<5} running   PID {pid}")
        elif stray:
            running += 1
            print(f"{name:<5} running   PID {stray}  (not started by this launcher)")
        else:
            print(f"{name:<5} stopped             log: {lf}")
    return 0 if running == len(_wanted(side)) else 1


def ensure(side='both', quiet=False):
    """Restart any panel that has died. This is the watchdog.

    The right panel died silently on 17 August 2026 and left a stale pidfile
    behind; nothing noticed and nothing restarted it. Liveness is therefore
    checked against the process table, not the pidfile alone — a stale pidfile
    is precisely that failure, and a PID can also have been recycled by an
    unrelated process.

    It is deliberately quiet when there is no graphical session. Logged out,
    the panels SHOULD be absent and cannot be started; that is not a failure,
    so it is not reported as one. The recorder runs as its own service exactly
    so that statistics keep accruing in that state.
    """
    env = _resolve_display(dict(os.environ))
    if not _display_ready(env, wait=0):
        return 0                              # no session: nothing to do
    dead = [n for n in _wanted(side) if not (_read_pid(n) or _find_running(n))]
    if not dead:
        return 0
    print(f"panels down: {' '.join(dead)} — restarting", flush=True)
    rc = 0
    for name in dead:
        rc |= start(name, quiet=quiet)
    return rc


def restart(side='both', quiet=False):
    stop(side, quiet=quiet)
    time.sleep(1.0)
    return start(side, quiet=quiet)


def launch(action='start', side='both'):
    return {'start': start, 'stop': stop, 'status': status,
            'restart': restart, 'ensure': ensure}[action](side)
