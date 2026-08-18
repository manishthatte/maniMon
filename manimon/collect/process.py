"""
Process table, and the recognition of long-running jobs.

A 'job' is whatever the configuration says it is — see jobs.* in the config
file. Progress and ETA are parsed from the job's own output file.
"""

import os, re, glob, time

from ..util import rf, HZ, PAGE
from ..config import JOB_ROOTS, JOB_BINS, JOB_ID_RE, MPI_LAUNCHERS


# ═══════════════════════════════════════════════════════════════════════════════
#  Processes
# ═══════════════════════════════════════════════════════════════════════════════
class ProcCollector:
    """Reads /proc directly — no psutil dependency, no ps subprocess."""

    def __init__(self):
        self._prev = {}
        self._t = time.monotonic()
        self._boot = self._boot_time()

    @staticmethod
    def _boot_time():
        for line in rf('/proc/stat').split('\n'):
            if line.startswith('btime'):
                return int(line.split()[1])
        return int(time.time())

    def read(self):
        now = time.monotonic()
        dt = max(now - self._t, 0.001)
        self._t = now
        wall = time.time()
        cur, out = {}, []

        for d in glob.glob('/proc/[0-9]*'):
            pid = os.path.basename(d)
            stat = rf(f'{d}/stat')
            if not stat:
                continue
            try:
                rp = stat.rindex(')')
                comm = stat[stat.index('(') + 1:rp]
                f = stat[rp + 2:].split()
                utime, stime = int(f[11]), int(f[12])
                threads = int(f[17])
                start = int(f[19])
            except Exception:
                continue
            jiff = utime + stime
            cur[pid] = jiff
            prev = self._prev.get(pid)
            cpu = ((jiff - prev) / HZ) / dt * 100 if prev is not None else 0.0
            rss = 0
            for line in rf(f'{d}/statm').split():
                rss = int(line) if False else rss
            sm = rf(f'{d}/statm').split()
            if len(sm) >= 2:
                rss = int(sm[1]) * PAGE
            cmdline = rf(f'{d}/cmdline').replace('\x00', ' ').strip()
            out.append({
                'pid': int(pid),
                'comm': comm,
                'cmdline': cmdline or comm,
                'cpu': round(max(cpu, 0.0), 1),
                'rss': rss,
                'threads': threads,
                'elapsed': max(wall - (self._boot + start / HZ), 0),
                # Exact start epoch, from btime + the kernel's starttime field.
                # PIDs are reused, so this is what makes a run identifiable
                # across samples and across a recorder restart — `elapsed`
                # drifts with the sampling instant, this does not.
                'start_ts': self._boot + start / HZ,
            })
        self._prev = cur
        out.sort(key=lambda p: -p['cpu'])
        return out


def cwd_of(pid):
    try:
        return os.readlink(f'/proc/{pid}/cwd')
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
#  Simulations — identification, progress, ETA
# ═══════════════════════════════════════════════════════════════════════════════
# JOB_ID_RE comes from config (jobs.id_regex); when unset, a job is named
# after its executable instead of a pattern in its command line.
OUT_EXT = ('.out', '.log', '.txt', '.err', '.dat', '.stdout')


def _sim_id(cmdline):
    """A short display name for a job, from its command line.

    Returns None when no id_regex is configured — the caller then falls back to
    the executable name, which is always available.
    """
    if JOB_ID_RE is None:
        return None
    for tok in cmdline.split():
        m = JOB_ID_RE.search(os.path.basename(tok))
        if m:
            return m.group(1).replace('_', '-') if tok else None
    m = JOB_ID_RE.search(cmdline)
    return m.group(1) if m else None


def is_sim(proc):
    """True when the process is real physics work rather than desktop noise."""
    comm, cmd = proc['comm'], proc['cmdline']
    if comm in JOB_BINS or comm in MPI_LAUNCHERS:
        return True
    if comm.startswith(('python', 'lmp', 'pw.', 'gpaw')):
        if any(root in cmd for root in JOB_ROOTS):
            return True
        if JOB_ID_RE is not None and JOB_ID_RE.search(cmd):
            return True
    return False


def _output_file(pid):
    """
    The file the job is writing. Prefer an open write-mode fd (works regardless
    of cwd); fall back to the newest matching file in the process cwd.
    """
    best, best_m = None, -1
    for fd in glob.glob(f'/proc/{pid}/fd/*'):
        try:
            tgt = os.readlink(fd)
            if not tgt.startswith('/') or not tgt.endswith(OUT_EXT):
                continue
            m = os.path.getmtime(tgt)
        except Exception:
            continue
        if m > best_m:
            best, best_m = tgt, m
    if best:
        return best
    cwd = cwd_of(pid)
    if not cwd or not os.path.isdir(cwd):
        return None
    for f in glob.glob(f'{cwd}/*'):
        if not f.endswith(OUT_EXT):
            continue
        try:
            m = os.path.getmtime(f)
        except Exception:
            continue
        if m > best_m:
            best, best_m = f, m
    return best


def _tail(path, nbytes=8192):
    try:
        sz = os.path.getsize(path)
        with open(path, 'rb') as fh:
            fh.seek(max(0, sz - nbytes))
            return fh.read().decode('utf-8', 'replace').split('\n')
    except Exception:
        return []


def parse_progress(lines):
    """
    Return (text, fraction|None). Fraction drives the bar and the ETA.
    Engine-specific patterns first, then generic fallbacks.
    """
    text, frac = '', None
    lam_step = lam_total = None
    qe_iter = None

    for ln in lines:
        s = ln.strip()
        if not s:
            continue

        # LAMMPS — "run N" declares the total, data rows carry the step
        m = re.match(r'^run\s+(\d+)\s*$', s)
        if m:
            lam_total = int(m.group(1))
            continue
        m = re.match(r'^\s*(\d+)\s+[-\d.eE+]+\s+[-\d.eE+]+', ln)
        if m and lam_total:
            lam_step = int(m.group(1))
            continue

        # Quantum ESPRESSO
        m = re.search(r'iteration #\s*(\d+)', s)
        if m:
            qe_iter = int(m.group(1))
            continue
        if s.startswith('!') and 'total energy' in s:
            text = s[:70]
            continue

        # GPAW / ASE
        m = re.match(r'^iter:\s*(\d+)', s)
        if m:
            qe_iter = int(m.group(1))
            continue

        # Generic "N/M"
        m = re.search(r'\b(\d+)\s*/\s*(\d+)\b', s)
        if m and int(m.group(2)) > 0:
            a, b = int(m.group(1)), int(m.group(2))
            if a <= b:
                frac, text = a / b, s[:70]
                continue

        # Generic percentage
        m = re.search(r'\b(\d{1,3}(?:\.\d+)?)\s*%', s)
        if m:
            v = float(m.group(1))
            if 0 <= v <= 100:
                frac, text = v / 100.0, s[:70]

    if lam_step is not None and lam_total:
        frac = min(lam_step / lam_total, 1.0)
        text = f'step {lam_step:,}/{lam_total:,}'
    elif qe_iter is not None and not text:
        text = f'iteration {qe_iter}'

    if not text:
        for ln in reversed(lines):
            if ln.strip():
                text = ln.strip()[:70]
                break
    return text, frac


def running_sims(procs):
    """Physics jobs with progress and ETA where derivable."""
    out = []
    for p in procs:
        if not is_sim(p):
            continue
        if p['comm'] in MPI_LAUNCHERS and p['cpu'] < 1:
            continue
        path = _output_file(p['pid'])
        text, frac = ('', None)
        if path:
            text, frac = parse_progress(_tail(path))
        eta = None
        if frac and 0.01 < frac < 1.0:
            eta = p['elapsed'] * (1 - frac) / frac
        out.append({
            'pid': p['pid'],
            'sim_id': _sim_id(p['cmdline']) or p['comm'],
            'comm': p['comm'],
            'cpu': p['cpu'],
            'rss': p['rss'],
            'threads': p['threads'],
            'elapsed': p['elapsed'],
            'start_ts': p.get('start_ts'),
            'cmdline': p['cmdline'],
            'outfile': os.path.basename(path) if path else '',
            'progress': text,
            'frac': frac,
            'eta': eta,
        })
    out.sort(key=lambda s: -s['cpu'])
    return out
