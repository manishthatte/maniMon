#!/usr/bin/env python3
"""
maniMon — per-simulation resource accounting.

The metric store answers "how hot did the machine get last night". This module
answers the question that actually matters when planning work:

    what did THAT run cost?

    python3 runs.py                     runs from the last 7 days
    python3 runs.py --days 30           a longer window
    python3 runs.py --active            only what is running right now
    python3 runs.py --id relax_8_8      one simulation

WHAT IS AND IS NOT ATTRIBUTED
─────────────────────────────
Two kinds of number come out of this, and conflating them would be dishonest.

EXCLUSIVE — measured per process, so genuinely this run's:
    CPU-seconds, mean and peak CPU%, peak RSS, peak thread count, wall time.
These integrate the process's own counters. If two runs overlap, each one's
CPU-seconds are still its own.

SHARED — machine-wide, sampled over the run's window and NOT divided up:
    GPU power and energy, junction/memory temperature, board fan, package
    watts, whole-machine kWh.
A single W7900 feeding three concurrent jobs draws one board power. Splitting
that three ways would invent a number no sensor measured. So shared figures
are reported as "what the machine did while this run was up", together with
`concurrent` — the largest number of simulations running at any moment inside
the window. concurrent = 1 means the run really did have the machine to
itself and the shared figures ARE its cost. concurrent > 1 means they are an
upper bound shared with others, and the report says so rather than dividing.

This matters here specifically: the standing directive is to co-schedule CPU
simulations with GPU work rather than run them one at a time, so overlap is
the normal case, not an edge case.

Author: Manish Jagdish Thatte
"""

import os
import sys
import time

from . import metrics

# A run is closed once its process has not been seen for this long. Sampling is
# every RAW_EVERY seconds, so this tolerates a few missed samples (a paused
# recorder, a slow tick) without prematurely ending a live run.
STALE_AFTER = 120.0


def _schema(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            run_key   TEXT PRIMARY KEY,   -- pid:start_ts, because PIDs are reused
            sim_id    TEXT,
            comm      TEXT,
            cmdline   TEXT,
            pid       INTEGER,
            started   REAL,
            last_seen REAL,
            ended     REAL,               -- NULL while running
            samples   INTEGER DEFAULT 0,
            cpu_sec   REAL DEFAULT 0,     -- integrated, exclusive to this run
            cpu_max   REAL DEFAULT 0,
            rss_max   REAL DEFAULT 0,
            threads_max INTEGER DEFAULT 0,
            outfile   TEXT,
            progress  TEXT,
            frac      REAL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_runs_sim ON runs(sim_id)")
    con.commit()


def run_key(sim):
    """Stable identity for a run: PID plus its exact start epoch.

    PID alone is not enough — Linux reuses them, and a long campaign will wrap
    the PID space. start_ts comes from the kernel's own starttime field, so it
    does not drift with the sampling instant.
    """
    st = sim.get('start_ts')
    if st is None:                     # pre-start_ts snapshot; degrade, don't crash
        st = time.time() - (sim.get('elapsed') or 0)
    return f"{sim.get('pid')}:{int(st)}"


class RunTracker:
    """Folds each sample's running-sim list into the runs table.

    Same failure policy as metrics.Recorder: never let a database problem reach
    a caller, but never fail silently either — the first error is printed.
    """

    def __init__(self, path=metrics.DB_PATH):
        self.last_error = None
        self._warned = False
        self._con = None
        self._seen = {}                 # run_key -> last sample time
        try:
            self._con = metrics._connect(path)
            _schema(self._con)
        except Exception as e:
            self._fail(e)

    def _fail(self, exc):
        self.last_error = str(exc)[:120]
        if not self._warned:
            self._warned = True
            print(f"runs: tracking FAILED — {self.last_error}",
                  file=sys.stderr, flush=True)

    def update(self, snap, now=None):
        """Record one sample's worth of every running simulation."""
        if self._con is None:
            return False
        now = now or time.time()
        sims = snap.get('sims') or []
        try:
            for s in sims:
                key = run_key(s)
                prev = self._seen.get(key)
                # Seconds of wall time this sample stands for. On the first
                # sighting there is no interval yet, so it contributes 0 —
                # better than assuming a full period for a run we just met.
                dt = 0.0 if prev is None else max(0.0, min(now - prev, STALE_AFTER))
                self._seen[key] = now
                cpu = s.get('cpu') or 0.0
                self._con.execute("""
                    INSERT INTO runs (run_key, sim_id, comm, cmdline, pid,
                                      started, last_seen, samples, cpu_sec,
                                      cpu_max, rss_max, threads_max,
                                      outfile, progress, frac)
                    VALUES (?,?,?,?,?,?,?,1,?,?,?,?,?,?,?)
                    ON CONFLICT(run_key) DO UPDATE SET
                        last_seen   = excluded.last_seen,
                        samples     = runs.samples + 1,
                        cpu_sec     = runs.cpu_sec + excluded.cpu_sec,
                        cpu_max     = MAX(runs.cpu_max, excluded.cpu_max),
                        rss_max     = MAX(runs.rss_max, excluded.rss_max),
                        threads_max = MAX(runs.threads_max, excluded.threads_max),
                        outfile     = COALESCE(excluded.outfile, runs.outfile),
                        progress    = COALESCE(excluded.progress, runs.progress),
                        frac        = COALESCE(excluded.frac, runs.frac),
                        ended       = NULL
                """, (key, s.get('sim_id'), s.get('comm'), s.get('cmdline'),
                      s.get('pid'), now - (s.get('elapsed') or 0), now,
                      cpu * dt / 100.0, cpu, s.get('rss') or 0,
                      s.get('threads') or 0, s.get('outfile') or None,
                      s.get('progress') or None, s.get('frac')))
            # Close anything that has stopped appearing.
            self._con.execute(
                "UPDATE runs SET ended = last_seen "
                "WHERE ended IS NULL AND last_seen < ?", (now - STALE_AFTER,))
            self._con.commit()
        except Exception as e:
            self._fail(e)
            return False
        return True

    def close(self):
        try:
            if self._con:
                self._con.close()
                self._con = None
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
#  Reading back
# ═══════════════════════════════════════════════════════════════════════════════
class RunReader:
    def __init__(self, path=metrics.DB_PATH):
        self.path = path
        self._con = None
        try:
            if os.path.exists(path):
                self._con = metrics._connect(path)
                _schema(self._con)
        except Exception:
            self._con = None

    @property
    def available(self):
        return self._con is not None

    # Same ownership rule as metrics.Reader — see the note there.
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

    def list(self, days=7, sim_id=None, active_only=False, limit=50):
        if self._con is None:
            return []
        since = time.time() - days * 86400
        sql = "SELECT * FROM runs WHERE last_seen >= ?"
        args = [since]
        if sim_id:
            sql += " AND sim_id LIKE ?"
            args.append(f"%{sim_id}%")
        if active_only:
            sql += " AND ended IS NULL"
        sql += " ORDER BY started DESC LIMIT ?"
        args.append(limit)
        try:
            cur = self._con.execute(sql, args)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        except Exception:
            return []

    def enrich(self, run, stats=None):
        """Attach the SHARED machine figures for this run's window.

        Deliberately not divided between overlapping runs — see the module
        docstring. `concurrent` says how much company the run had, which is
        what makes the shared numbers interpretable.
        """
        stats = stats or metrics.Reader(self.path)
        a = run['started']
        b = run['ended'] or run['last_seen']
        out = dict(run)
        out['wall'] = max(0.0, b - a)
        out['concurrent'] = self.concurrent(a, b)
        if not stats.available:
            return out
        win = (a, b)
        for label, col in (('gpu_junction_max', 'gpu_temp_junction'),
                           ('gpu_mem_max', 'gpu_temp_mem'),
                           ('cpu_temp_max', 'cpu_temp')):
            try:
                p = stats.peak(col, window=win)
                out[label] = p['value'] if p else None
            except Exception:
                out[label] = None
        try:
            out['wh'] = (stats.energy_wh(window=win) or {}).get('total')
        except Exception:
            out['wh'] = None
        try:
            out['gpu_busy_mean'] = (stats.stats('gpu_busy', window=win) or {}).get('mean')
        except Exception:
            out['gpu_busy_mean'] = None
        return out

    def concurrent(self, a, b):
        """Largest number of runs overlapping this window at any one moment.

        Computed by sweeping the interval endpoints rather than sampling, so a
        brief overlap cannot be missed between samples.
        """
        if self._con is None:
            return 1
        try:
            rows = self._con.execute(
                "SELECT started, COALESCE(ended, last_seen) FROM runs "
                "WHERE started <= ? AND COALESCE(ended, last_seen) >= ?",
                (b, a)).fetchall()
        except Exception:
            return 1
        events = []
        for s, e in rows:
            events.append((max(s, a), 1))
            events.append((min(e, b), -1))
        events.sort()
        best = cur = 0
        for _, d in events:
            cur += d
            best = max(best, cur)
        return max(best, 1)


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════════
def _dur(s):
    if s is None:
        return "—"
    s = int(s)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}"
    return f"{s // 3600}h{(s % 3600) // 60:02d}"


def _gb(b):
    return "—" if not b else f"{b / 1024**3:.1f}G"


def report(days=7, sim_id=None, active_only=False):
    rr = RunReader()
    if not rr.available:
        print("no run history yet — is the recorder running?")
        print("  systemctl --user status manimon-metrics.service")
        return 1
    runs = rr.list(days=days, sim_id=sim_id, active_only=active_only)
    if not runs:
        print(f"no runs in the last {days} days"
              + (f" matching {sim_id!r}" if sim_id else ""))
        return 0

    stats = metrics.Reader()
    print(f"maniMon job runs — last {days} days"
          + (f", id~{sim_id}" if sim_id else "")
          + (", active only" if active_only else ""))
    print("=" * 96)
    print(f"{'simulation':<26} {'started':<12} {'wall':>7} {'cpu-h':>7} "
          f"{'peak RSS':>9} {'thr':>4} {'GPUjc':>6} {'Wh':>7}  with")
    print("-" * 96)
    for r in runs:
        e = rr.enrich(r, stats)
        started = time.strftime('%d %b %H:%M', time.localtime(r['started']))
        conc = e['concurrent']
        # A shared figure is only this run's cost when it ran alone.
        shared = "alone" if conc <= 1 else f"{conc - 1} other{'s' if conc > 2 else ''}"
        jc = f"{e.get('gpu_junction_max'):.0f}" if e.get('gpu_junction_max') else "—"
        wh = f"{e.get('wh'):.1f}" if e.get('wh') else "—"
        name = (r['sim_id'] or r['comm'] or '?')[:26]
        live = "" if r['ended'] else " *"
        print(f"{name:<26} {started:<12} {_dur(e['wall']):>7} "
              f"{r['cpu_sec'] / 3600:>7.2f} {_gb(r['rss_max']):>9} "
              f"{r['threads_max']:>4} {jc:>6} {wh:>7}  {shared}{live}")
    print()
    print("  * still running.  cpu-h and peak RSS are exclusive to the run.")
    print("  GPUjc and Wh are machine-wide over the run's window and are NOT")
    print("  split between concurrent runs — see the 'with' column.")
    return 0


def _main():
    days = 7
    sim_id = None
    active = '--active' in sys.argv
    if '--days' in sys.argv:
        try:
            days = float(sys.argv[sys.argv.index('--days') + 1])
        except (IndexError, ValueError):
            pass
    if '--id' in sys.argv:
        try:
            sim_id = sys.argv[sys.argv.index('--id') + 1]
        except IndexError:
            pass
    return report(days=days, sim_id=sim_id, active_only=active)


if __name__ == '__main__':
    sys.exit(_main())
