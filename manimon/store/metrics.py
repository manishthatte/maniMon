#!/usr/bin/env python3
"""
maniMon — the metric store.

Before this existed the monitor kept NO history at all: every sparkline was an
in-memory ring buffer inside a panel process, so restarting a panel erased it
and nothing survived a reboot. Questions like "how hot did the GPU get during
last night's GW run", "is the NVMe throttling", "how many watt-hours did that
6 h LAMMPS job cost" were simply unanswerable. Adding more sensors without a
store just adds more numbers that vanish.

    python3 metrics.py --report            statistics digest, no X server
    python3 metrics.py --report --hours 6  over a different window
    python3 metrics.py --record            headless recorder (no panels needed)
    python3 metrics.py --info              size, row counts, retention state

DESIGN
──────
SQLite on /home (survives an OS reinstall — standing rule), WAL mode so the
panels can read while one process writes. Three resolutions in one table,
folded up by age:

    res   period   kept for    rows/day
    r     10 s     48 h        8 640
    1m    1 min    30 d        1 440
    10m   10 min   forever       144

Steady state is roughly 400 MB/year, and retention is ENFORCED on every fold,
not left to hope. Downsampling stores the MEAN for rates and the MAX for
temperatures and power — averaging a thermal peak away would defeat the point
of keeping the history.

Author: Manish Jagdish Thatte
"""

import math
import os
import sqlite3
import sys
import threading
import time

# Location comes from config.py so a site can move the store onto a different
# filesystem — the default keeps it under ~/.local/state, which survives an OS
# reinstall on machines where /home is a separate partition.
from ..config import STATE_DIR
DB_PATH = f"{STATE_DIR}/metrics.db"

RAW_EVERY = 10.0            # seconds between raw samples
RETENTION = {'r': 48 * 3600, '1m': 30 * 86400, '10m': None}
FOLD_EVERY = 600.0          # run the fold/prune pass this often

# column -> how it downsamples. 'max' for anything where the peak is the point.
COLUMNS = {
    'cpu_pct': 'avg', 'cpu_temp': 'max', 'cpu_power': 'avg', 'cpu_freq': 'avg',
    'load1': 'avg', 'runq': 'max',
    'psi_cpu': 'max', 'psi_io': 'max', 'psi_mem': 'max',
    'mem_used_gb': 'avg', 'mem_cache_gb': 'avg', 'swap_used_gb': 'max',
    'gpu_busy': 'avg', 'gpu_mem_pct': 'avg',
    'gpu_temp_edge': 'max', 'gpu_temp_junction': 'max', 'gpu_temp_mem': 'max',
    'gpu_power': 'avg', 'gpu_fan_rpm': 'max', 'gpu_sclk': 'avg',
    'gpu_throttled': 'max',
    'nvme_temp': 'max', 'home_temp': 'max',
    'disk_r_bps': 'avg', 'disk_w_bps': 'avg',
    'net_rx_bps': 'avg', 'net_tx_bps': 'avg', 'nic_temp': 'max',
    'ecc_ce': 'max', 'ecc_ue': 'max',
    'root_pct': 'avg', 'home_pct': 'avg', 'var_pct': 'avg',
    'bmc_fan_max': 'max', 'bmc_temp_max': 'max', 'bmc_power': 'avg',
    'n_sims': 'max',
}

# Anything a "how bad did it get" question is asked about.
PEAK_COLUMNS = [c for c, how in COLUMNS.items() if how == 'max']


def _connect(path=DB_PATH, timeout=5.0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # check_same_thread=False is REQUIRED, not a convenience. A panel builds its
    # Collector — and therefore its Recorder — on the GTK main thread, but every
    # sample arrives from the collector worker thread. Python's default binds a
    # connection to its creating thread and raises ProgrammingError on use from
    # any other, which Recorder.record() would swallow into last_error: the
    # store would sit at zero rows forever while the panel looked healthy.
    # (That is exactly what happened between 18 Aug 03:59 and the reboot.)
    # Safe here because callers serialise every use through Recorder._lock.
    con = sqlite3.connect(path, timeout=timeout, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=5000")
    return con


def _schema(con):
    cols = ",\n            ".join(f"{c} REAL" for c in COLUMNS)
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS samples (
            res TEXT NOT NULL,
            ts  INTEGER NOT NULL,
            {cols},
            PRIMARY KEY (res, ts)
        ) WITHOUT ROWID
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_res_ts ON samples(res, ts)")
    con.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY, value TEXT
        )
    """)

    # Migrate. CREATE TABLE IF NOT EXISTS is a no-op on an existing store, so
    # without this every column added to COLUMNS after the first run would make
    # each INSERT fail on an unknown column — for the whole row, not just the
    # new metric. Old rows simply carry NULL for the new column, which is the
    # honest value: that sensor was not being read then.
    have = {r[1] for r in con.execute("PRAGMA table_info(samples)")}
    for c in COLUMNS:
        if c not in have:
            con.execute(f"ALTER TABLE samples ADD COLUMN {c} REAL")
    con.commit()


# ═══════════════════════════════════════════════════════════════════════════════
#  Extracting one row from a collector snapshot
# ═══════════════════════════════════════════════════════════════════════════════
def _g(d, *path, default=None):
    """Nested get that tolerates missing keys and None at any level."""
    for p in path:
        if d is None:
            return default
        if isinstance(d, dict):
            d = d.get(p)
        elif isinstance(d, (list, tuple)):
            try:
                d = d[p]
            except (IndexError, TypeError):
                return default
        else:
            return default
    return default if d is None else d


def row_from_snapshot(snap):
    """
    Flatten a collector snapshot into one metric row.

    Everything is optional. A missing sensor stores NULL, never 0 — the
    difference between "the GPU drew no power" and "we could not read the GPU"
    is exactly the kind of thing this store exists to preserve.
    """
    gpus = snap.get('gpus') or []
    g0 = gpus[0] if gpus else {}
    disks = snap.get('disks') or []
    diskio = snap.get('diskio') or {}
    nets = [n for n in (snap.get('net') or []) if n.get('up')]
    n0 = nets[0] if nets else {}

    def mount_pct(target):
        for dev in disks:
            for part in dev.get('parts', []):
                if part.get('mount') == target:
                    return part.get('pct')
        return None

    def dev_temp(name):
        for dev in disks:
            if dev.get('dev') == name:
                return dev.get('temp')
        return None

    def mount_temp(target):
        """Temperature of the disk carrying `target`, found by mount point.

        Never by device letter. On the 18 Aug 2026 boot the 7.68 TB SSD came
        up as sdc rather than sda because two USB backup drives claimed the
        earlier letters first, so a hardcoded 'sda' logged NULL for /home's
        disk and a temperature for a backup drive that is usually unplugged.
        """
        for dev in disks:
            for part in dev.get('parts', []):
                if part.get('mount') == target:
                    return dev.get('temp')
        return None

    # `sum(...) or None` turned a genuine zero into NULL, which is exactly the
    # conflation this module promises not to make: an idle disk reads 0 B/s,
    # and that is a fact worth storing. NULL is reserved for "no counters at
    # all", i.e. diskio itself came back empty.
    r_bps = sum(v.get('r_bps', 0) for v in diskio.values()) if diskio else None
    w_bps = sum(v.get('w_bps', 0) for v in diskio.values()) if diskio else None

    bmc = snap.get('bmc') or {}
    ecc = snap.get('ecc') or {}

    return {
        'cpu_pct':    _g(snap, 'cpu', 'agg', 'total'),
        'cpu_temp':   _g(snap, 'cpu', 'temp'),
        'cpu_power':  _g(snap, 'cpu', 'power'),
        'cpu_freq':   _g(snap, 'cpu', 'freq_avg'),
        'load1':      _g(snap, 'cpu', 'loadavg', 0),
        'runq':       _g(snap, 'cpu', 'runq'),
        'psi_cpu':    _g(snap, 'pressure', 'cpu', 'some_avg10'),
        'psi_io':     _g(snap, 'pressure', 'io', 'some_avg10'),
        'psi_mem':    _g(snap, 'pressure', 'memory', 'some_avg10'),
        'mem_used_gb':  _g(snap, 'mem', 'used_gb'),
        'mem_cache_gb': _g(snap, 'mem', 'cache_gb'),
        'swap_used_gb': _g(snap, 'mem', 'swap_used_gb'),
        'gpu_busy':          g0.get('busy'),
        'gpu_mem_pct':       g0.get('vram_pct'),
        'gpu_temp_edge':     g0.get('temp_edge'),
        'gpu_temp_junction': g0.get('temp_junction'),
        'gpu_temp_mem':      g0.get('temp_mem'),
        'gpu_power':         g0.get('power'),
        'gpu_fan_rpm':       g0.get('fan_rpm'),
        'gpu_sclk':          g0.get('sclk'),
        'gpu_throttled':     1 if g0.get('throttled') else 0,
        'nvme_temp':  dev_temp('nvme0n1'),
        'home_temp':  mount_temp('/home'),
        'disk_r_bps': r_bps,
        'disk_w_bps': w_bps,
        'net_rx_bps': n0.get('rx_bps'),
        'net_tx_bps': n0.get('tx_bps'),
        'nic_temp':   n0.get('temp'),
        'ecc_ce':     ecc.get('ce'),
        'ecc_ue':     ecc.get('ue'),
        'root_pct':   mount_pct('/'),
        'home_pct':   mount_pct('/home'),
        'var_pct':    mount_pct('/var'),
        'bmc_fan_max':  bmc.get('fan_max'),
        'bmc_temp_max': bmc.get('temp_max'),
        'bmc_power':    bmc.get('power'),
        'n_sims':     len(snap.get('sims') or []),
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  Writer
# ═══════════════════════════════════════════════════════════════════════════════
class Recorder:
    """
    Owns the single write connection. One process should hold one of these;
    the panels that only display statistics use `Reader` instead.

    Failure policy: a recording error must never take down a panel, so every
    public method swallows exceptions and records the last one for the footer
    to show. A monitor that crashes the desktop because its database is busy
    would be worse than no monitor.
    """

    def __init__(self, path=DB_PATH):
        self.path = path
        self.last_error = None
        self._last_write = 0.0
        self._last_fold = time.monotonic()
        self._con = None
        # Guards every use of the connection, because it is opened with
        # check_same_thread=False (see _connect) and construction, recording
        # and close() can each happen on a different thread.
        self._lock = threading.Lock()
        self._warned = False
        try:
            self._con = _connect(path)
            _schema(self._con)
        except Exception as e:
            self._fail(e)

    def record(self, snap, now=None):
        """Store one raw sample if RAW_EVERY has elapsed. Returns True if written."""
        if self._con is None:
            return False
        now = now or time.time()
        if now - self._last_write < RAW_EVERY:
            return False
        try:
            row = row_from_snapshot(snap)
            cols = list(row)
            sql = (f"INSERT OR REPLACE INTO samples (res, ts, {','.join(cols)}) "
                   f"VALUES (?,?,{','.join('?' * len(cols))})")
            with self._lock:
                self._con.execute(sql, ['r', int(now)] + [row[c] for c in cols])
                self._con.commit()
            self._last_write = now
        except Exception as e:
            self._fail(e)
            return False
        self._maybe_fold()
        return True

    def _fail(self, exc):
        """Record an error, and shout the FIRST one to the panel log.

        A recorder that fails silently is worse than one that crashes: the
        panel keeps drawing, the store stays empty, and nobody finds out until
        someone asks the store a question weeks later. One line on stderr is
        enough to make that impossible.
        """
        self.last_error = str(exc)[:120]
        if not self._warned:
            self._warned = True
            print(f"metrics: recording FAILED — {self.last_error}",
                  file=sys.stderr, flush=True)

    def _maybe_fold(self):
        if time.monotonic() - self._last_fold < FOLD_EVERY:
            return
        self._last_fold = time.monotonic()
        try:
            with self._lock:
                fold(self._con)
        except Exception as e:
            self._fail(e)

    def close(self):
        try:
            with self._lock:
                if self._con:
                    self._con.close()
                    self._con = None
        except Exception:
            pass


def fold(con, now=None):
    """
    Roll raw rows up into 1-minute means, 1-minute into 10-minute, then prune
    each resolution past its retention. Idempotent: re-running changes nothing.
    """
    now = int(now or time.time())
    for src, dst, bucket in (('r', '1m', 60), ('1m', '10m', 600)):
        cutoff = now - RETENTION[src]
        aggs = ", ".join(
            f"{'MAX' if how == 'max' else 'AVG'}({c}) AS {c}"
            for c, how in COLUMNS.items())
        con.execute(f"""
            INSERT OR REPLACE INTO samples (res, ts, {','.join(COLUMNS)})
            SELECT ?, (ts / {bucket}) * {bucket}, {aggs}
              FROM samples
             WHERE res = ? AND ts < ?
             GROUP BY ts / {bucket}
        """, (dst, src, cutoff))
        con.execute("DELETE FROM samples WHERE res = ? AND ts < ?", (src, cutoff))
    con.commit()


# ═══════════════════════════════════════════════════════════════════════════════
#  Reader — the statistics themselves
# ═══════════════════════════════════════════════════════════════════════════════
class Reader:
    """Read-only queries. Safe to open in every panel; WAL allows concurrency."""

    def __init__(self, path=DB_PATH):
        self.path = path
        self._con = None
        try:
            if os.path.exists(path):
                self._con = sqlite3.connect(f"file:{path}?mode=ro", uri=True,
                                            timeout=2.0)
                self._con.execute("PRAGMA busy_timeout=2000")
        except Exception:
            self._con = None

    @property
    def available(self):
        return self._con is not None

    # A Reader holds an open SQLite handle, so it owns a file descriptor and a
    # page cache until it is collected. The panels build one and keep it for
    # their lifetime, which is fine; every other caller builds one for a single
    # question — `manimon info`, doctor, the run accounting — and used to drop
    # it on the floor. That showed up as ResourceWarning: unclosed database
    # across the test suite, and on a long-lived process it is a slow leak.
    def close(self):
        try:
            if self._con is not None:
                self._con.close()
        except Exception:
            pass
        finally:
            self._con = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def __del__(self):
        self.close()

    def _rows(self, column, hours, window=None):
        """Every value of one column over the window, across all resolutions.

        `hours` is a window ending now. `window=(a, b)` overrides it with an
        absolute span, which is what per-run accounting needs: a run that ended
        yesterday cannot be described by a window anchored to the present.
        """
        if self._con is None:
            return []
        if window:
            a, b = int(window[0]), int(window[1])
        else:
            a, b = int(time.time() - hours * 3600), int(time.time()) + 1
        try:
            cur = self._con.execute(
                f"SELECT ts, {column} FROM samples "
                f"WHERE ts >= ? AND ts <= ? AND {column} IS NOT NULL ORDER BY ts",
                (a, b))
            return cur.fetchall()
        except Exception:
            return []

    def stats(self, column, hours=24, window=None):
        """min / mean / p95 / max / n over the window. None if no data."""
        vals = [v for _, v in self._rows(column, hours, window)]
        if not vals:
            return None
        vals_sorted = sorted(vals)
        n = len(vals_sorted)
        # Nearest-rank p95: for small n this is the honest choice, since
        # interpolating between two samples invents a value never observed.
        p95 = vals_sorted[min(n - 1, max(0, math.ceil(0.95 * n) - 1))]
        return {
            'min': vals_sorted[0],
            'mean': sum(vals) / n,
            'p95': p95,
            'max': vals_sorted[-1],
            'n': n,
        }

    def peak(self, column, hours=24 * 30, window=None):
        """(value, unix_ts) of the highest reading, so a peak can be dated."""
        rows = self._rows(column, hours, window)
        if not rows:
            return None
        ts, val = max(rows, key=lambda r: r[1])
        return {'value': val, 'ts': ts}

    def fraction_above(self, column, threshold, hours=24):
        """
        Fraction of SAMPLES above a threshold — this is what makes a standing
        rule like "GPU junction <= 85 C" measurable rather than aspirational.

        Sample fraction, not time fraction: resolutions are folded, so a 10 min
        row and a 10 s row would carry different weight. Over a 24 h window
        everything is raw or 1 m, so the two agree closely; over 30 days it
        skews toward the coarse rows and is reported as a sample fraction for
        that reason.
        """
        vals = [v for _, v in self._rows(column, hours)]
        if not vals:
            return None
        return sum(1 for v in vals if v > threshold) / len(vals)

    def energy_wh(self, hours=24, window=None):
        """
        Watt-hours from the power series, integrated by the trapezoid rule.

        Gaps matter: if the recorder was down for an hour, integrating straight
        across would invent energy that was never drawn. Any interval longer
        than 5x the raw period is treated as a gap and skipped.
        """
        out = {}
        for col, label in (('cpu_power', 'cpu'), ('gpu_power', 'gpu')):
            rows = self._rows(col, hours, window)
            if len(rows) < 2:
                continue
            wh = 0.0
            for (t0, v0), (t1, v1) in zip(rows, rows[1:]):
                dt = t1 - t0
                if dt <= 0 or dt > RAW_EVERY * 5:
                    continue
                wh += (v0 + v1) / 2 * dt / 3600.0
            out[label] = wh
        out['total'] = sum(out.values())
        return out

    def trend_per_day(self, column, hours=24):
        """
        Least-squares slope in units/day. Used for 'days until full' and for
        projecting NVMe wear. Returns None when the fit has nothing to say.
        """
        rows = self._rows(column, hours)
        if len(rows) < 10:
            return None
        t0 = rows[0][0]
        xs = [(t - t0) / 86400.0 for t, _ in rows]
        ys = [v for _, v in rows]
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        den = sum((x - mx) ** 2 for x in xs)
        if den <= 0:
            return None
        return sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / den

    def days_until(self, column, target=100.0, hours=24):
        """Days until a filling filesystem reaches `target` percent."""
        slope = self.trend_per_day(column, hours)
        rows = self._rows(column, hours)
        if not slope or slope <= 0 or not rows:
            return None
        current = rows[-1][1]
        if current >= target:
            return 0.0
        return (target - current) / slope

    def info(self):
        if self._con is None:
            return {'available': False}
        out = {'available': True, 'path': self.path,
               'size_mb': round(os.path.getsize(self.path) / 1e6, 1)}
        for res in ('r', '1m', '10m'):
            cur = self._con.execute(
                "SELECT COUNT(*), MIN(ts), MAX(ts) FROM samples WHERE res=?", (res,))
            n, lo, hi = cur.fetchone()
            out[res] = {'rows': n, 'from': lo, 'to': hi}
        return out


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════════
def _fmt_ts(ts):
    return time.strftime('%d %b %H:%M', time.localtime(ts)) if ts else '—'


def report(hours=24):
    r = Reader()
    if not r.available:
        print(f"No metric store yet at {DB_PATH}.")
        print("It is created once the left panel (or `metrics.py --record`) runs.")
        return 1

    print(f"maniMon machine statistics — last {hours} h")
    print("=" * 62)

    rows = [
        ('CPU busy',        'cpu_pct',           '%',   None),
        ('CPU Tctl',        'cpu_temp',          'C',   None),
        ('CPU package',     'cpu_power',         'W',   None),
        ('GPU busy',        'gpu_busy',          '%',   None),
        ('GPU junction',    'gpu_temp_junction', 'C',   85),
        ('GPU memory',      'gpu_temp_mem',      'C',   95),
        ('GPU board power', 'gpu_power',         'W',   None),
        ('NVMe',            'nvme_temp',         'C',   70),
        ('/home disk',      'home_temp',         'C',   60),
        ('NIC',             'nic_temp',          'C',   None),
        ('Memory used',     'mem_used_gb',       'GB',  None),
        ('Swap used',       'swap_used_gb',      'GB',  None),
        ('PSI io',          'psi_io',            '',    None),
    ]
    print(f"{'':17}{'min':>8}{'mean':>8}{'p95':>8}{'max':>8}   over")
    for label, col, unit, thresh in rows:
        s = r.stats(col, hours)
        if not s:
            continue
        extra = ''
        if thresh is not None:
            f = r.fraction_above(col, thresh, hours)
            if f is not None:
                extra = f"  >{thresh}{unit} for {f*100:.1f}% of samples"
        print(f"{label:17}{s['min']:8.1f}{s['mean']:8.1f}{s['p95']:8.1f}"
              f"{s['max']:8.1f}   {s['n']:>5} samples{extra}")

    print()
    e = r.energy_wh(hours)
    if e.get('total'):
        print(f"Energy       {e['total']/1000:.2f} kWh total"
              f"   (CPU {e.get('cpu', 0)/1000:.2f} + GPU {e.get('gpu', 0)/1000:.2f})")

    print()
    print("Peaks (all time)")
    for label, col in (('CPU Tctl', 'cpu_temp'), ('GPU junction', 'gpu_temp_junction'),
                       ('GPU memory', 'gpu_temp_mem'), ('NVMe', 'nvme_temp'),
                       ('/home disk', 'home_temp'), ('Swap', 'swap_used_gb')):
        p = r.peak(col)
        if p:
            print(f"  {label:14} {p['value']:7.1f}   on {_fmt_ts(p['ts'])}")

    print()
    print("Filesystem trend")
    for label, col in (('/', 'root_pct'), ('/home', 'home_pct'), ('/var', 'var_pct')):
        slope = r.trend_per_day(col, hours)
        if slope is None:
            continue
        d = r.days_until(col, 100.0, hours)
        when = f"full in {d:.0f} d" if d and d < 3650 else "not filling"
        print(f"  {label:8} {slope:+.3f} %/day   {when}")

    ce, ue = r.stats('ecc_ce', hours), r.stats('ecc_ue', hours)
    if ce or ue:
        print()
        print(f"ECC          correctable {ce['max'] if ce else 0:.0f}"
              f"   uncorrectable {ue['max'] if ue else 0:.0f}")

    i = r.info()
    print()
    print(f"Store        {i['size_mb']} MB   "
          f"raw {i['r']['rows']}  1m {i['1m']['rows']}  10m {i['10m']['rows']} rows")
    return 0


# Everything row_from_snapshot and the run tracker read. Narrower than a
# panel's needs — the recorder draws nothing, so it skips tmux, sockets, the
# journal, the repo, backups and the WAN probe entirely.
RECORD_WANT = {
    'cpu', 'pressure', 'mem', 'gpus', 'gpu_metrics', 'ecc',
    'disks', 'diskio', 'net', 'bmc', 'smart', 'procs', 'sims',
}


def record_loop():
    """Headless recorder — the authoritative writer.

    This is what the manimon-metrics user service runs. Recording deliberately
    does NOT live in a panel: a GTK panel dies with the graphical session, and
    then the history has a hole exactly across the overnight run whose thermals
    were the reason for keeping history at all.
    """
    import signal
    from ..collect import Collector
    from . import runs as runs_mod

    col = Collector(want=RECORD_WANT)
    rec = Recorder()
    if rec._con is None:
        print(f"cannot open {DB_PATH}: {rec.last_error}", file=sys.stderr)
        return 1
    tracker = runs_mod.RunTracker()

    stop = {'now': False}

    def _sigterm(signum, frame):
        # systemd stops services with SIGTERM. Without this the loop is killed
        # mid-iteration and the last samples never commit.
        stop['now'] = True

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)

    print(f"recording to {DB_PATH} every {RAW_EVERY:.0f}s", flush=True)
    n = 0
    first = True
    try:
        while not stop['now']:
            snap = col.tick(force_all=first)
            first = False
            if rec.record(snap):
                n += 1
                tracker.update(snap)
                if n % 180 == 0:                     # ~every 30 min
                    print(f"  {n} samples", flush=True)
            for _ in range(10):                      # responsive to SIGTERM
                if stop['now']:
                    break
                time.sleep(0.2)
    finally:
        print(f"stopped after {n} samples", flush=True)
        tracker.close()
        rec.close()
    return 0


def _main():
    args = sys.argv[1:]
    if '--record' in args:
        return record_loop()
    if '--info' in args:
        import json
        with Reader() as r:
            print(json.dumps(r.info(), indent=2, default=str))
        return 0
    if '--report' in args or not args:
        hours = 24.0
        if '--hours' in args:
            try:
                hours = float(args[args.index('--hours') + 1])
            except (IndexError, ValueError):
                pass
        return report(hours)
    print(__doc__)
    return 0


if __name__ == '__main__':
    sys.exit(_main())
