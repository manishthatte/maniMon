"""
maniMon — the data collection layer.

Pure readers. No GTK, no rendering, no X server. Every collector returns plain
dicts and lists of primitives, so a snapshot is JSON-serialisable and any front
end can consume it.

The readers live in sibling modules by subsystem; this file is only the facade
that ties them into one tick with three refresh tiers.

    python3 -m manimon.collect --dump
    python3 -m manimon.collect --dump cpu gpu mem
"""

import sys, time

from ..sensors import published
from .cpu import CPUCollector, pressure, numa_nodes
from .gpu import gpus, gpu_clients
from .memory import MemCollector
from .disk import DiskCollector
from .net import NetCollector, sockets
from .process import ProcCollector, running_sims, is_sim, parse_progress, cwd_of
from .jobs import TmuxCollector, campaign, recent_results
from .system import repo, backups, services, journal, sysinfo, taskbar_reserved
from .attention import attention, acknowledge, SEV_CRIT, SEV_WARN, SEV_INFO, SEV_OK

__all__ = [
    'Collector', 'published',
    'CPUCollector', 'MemCollector', 'DiskCollector', 'NetCollector',
    'ProcCollector', 'TmuxCollector',
    'pressure', 'numa_nodes', 'gpus', 'gpu_clients', 'sockets',
    'running_sims', 'is_sim', 'parse_progress', 'cwd_of',
    'campaign', 'recent_results',
    'repo', 'backups', 'services', 'journal', 'sysinfo', 'taskbar_reserved',
    'attention', 'acknowledge', 'SEV_CRIT', 'SEV_WARN', 'SEV_INFO', 'SEV_OK',
]


# ═══════════════════════════════════════════════════════════════════════════════
#  Facade — tiered sampling, thread-safe snapshot
# ═══════════════════════════════════════════════════════════════════════════════
class Collector:
    """
    FAST 2 s   — cpu, gpu, mem, net, disk io, pressure
    SLOW 10 s  — mounts, processes, sims, tmux, gpu clients, sockets
    LAZY 60 s  — services, journal, repo, backups, campaign, results, sysinfo

    Every tier is wrapped so one failing reader cannot take down the panel;
    the previous good value is retained instead.

    A panel passes `want` so it pays only for what it renders — the left panel
    never spawns a tmux or journalctl subprocess.
    """
    SLOW_EVERY = 10.0
    LAZY_EVERY = 60.0

    def __init__(self, want=None, record=False):
        self.want = set(want) if want else None
        self.cpu = CPUCollector()
        self.mem = MemCollector()
        self.disk = DiskCollector()
        self.net = NetCollector()
        self.proc = ProcCollector()
        self.tmux = TmuxCollector()
        self.snap = {}
        self._slow_at = 0.0
        self._lazy_at = 0.0
        # Exactly one process should record — the left panel, which collects
        # the hardware series. A second writer is harmless (WAL + INSERT OR
        # REPLACE on the same second) but wasteful.
        self.recorder = None
        if record:
            try:
                from ..store import metrics
                self.recorder = metrics.Recorder()
            except Exception as e:
                # Say so. A caller that asked for record=True and silently got
                # no recorder has no way to find out except by noticing an
                # empty database later.
                print(f"collector: metric recording unavailable — {e}",
                      file=sys.stderr, flush=True)
                self.recorder = None

    def _try(self, key, fn, default):
        """Run one reader, substituting a default if it raises.

        The failure is recorded in snap['_errors'][key], which the attention
        engine turns into a visible item. That link did not exist until
        19 Aug 2026: the field was written here from the start and read
        absolutely nowhere, so a reader that threw on every tick left the panel
        showing its default — an empty dict, a zero, a blank list — with no hint
        that the number on screen had stopped meaning anything.

        The entry is cleared again on the next success. Without that a single
        transient error latches for the lifetime of the process, because
        self.snap persists across ticks.
        """
        if self.want is not None and key not in self.want:
            return
        try:
            self.snap[key] = fn()
        except Exception as e:
            self.snap.setdefault(key, default)
            self.snap.setdefault('_errors', {})[key] = str(e)[:80]
        else:
            self.snap.get('_errors', {}).pop(key, None)

    def tick(self, force_all=False):
        now = time.monotonic()

        self._try('cpu', self.cpu.read, {})
        self._try('pressure', pressure, {})
        self._try('gpus', gpus, [])
        self._try('mem', self.mem.read, {})
        self._try('net', self.net.read, [])
        self._try('diskio', self.disk.io, {})
        self._try('numa', lambda: numa_nodes(self.snap.get('cpu', {}).get('per_cpu', [])), [])

        # gpu_metrics is a single 120-byte read of a world-readable sysfs file
        # — cheap enough for the fast tier, and it carries the throttle state
        # and VR temperatures that nothing else exposes.
        self._try('gpu_metrics', published.gpu_metrics, {})
        self._try('ecc', published.ecc, {})

        if force_all or now - self._slow_at >= self.SLOW_EVERY:
            self._slow_at = now
            self._try('disks', self.disk.mounts, [])
            # Published by sensord as root; reading them is just a JSON load.
            self._try('bmc', published.bmc, {})
            self._try('smart', published.smart, {})
            self._try('peripherals', published.peripherals, [])
            self._try('procs', self.proc.read, [])
            self._try('sims', lambda: running_sims(self.snap.get('procs', [])), [])
            self._try('tmux', self.tmux.read, [])
            self._try('gpu_clients', gpu_clients, {})
            self._try('sockets', sockets, {})
            self._try('wan', self.net.wan, {})

        if force_all or now - self._lazy_at >= self.LAZY_EVERY:
            self._lazy_at = now
            self._try('services', services, {})
            self._try('journal', journal, {})
            self._try('repo', repo, {})
            self._try('backups', backups, [])
            self._try('campaign', campaign, {})
            self._try('recent', lambda: recent_results(24, 10), [])
            self._try('sysinfo', sysinfo, {})
            self._try('dimms', published.dimms, {})

        self._try('attention', lambda: attention(self.snap), [])
        self.snap['ts'] = time.time()

        if self.recorder is not None:
            # A database problem must not reach the UI thread — but it must not
            # disappear either. Recording once failed for an entire session
            # because a SQLite connection was bound to the wrong thread, every
            # insert raised, and the exception went into a field nobody read;
            # the dashboard looked healthy the whole time. It goes to the same
            # place every other reader failure goes, so the panel shows it.
            try:
                self.recorder.record(self.snap)
            except Exception as e:
                self.snap.setdefault('_errors', {})['recorder'] = str(e)[:80]
            else:
                self.snap.get('_errors', {}).pop('recorder', None)
        return self.snap


def _main(argv=None):
    """`python3 -m manimon.collect --dump [section ...]` — snapshot as JSON."""
    import json
    argv = list(sys.argv[1:] if argv is None else argv)
    if '--dump' not in argv:
        print(__doc__)
        return 0
    want = [a for a in argv if not a.startswith('-')]
    c = Collector()
    c.tick(force_all=True)
    time.sleep(1.0)                       # second sample so rates are non-zero
    snap = c.tick(force_all=True)
    if want:
        snap = {k: v for k, v in snap.items() if k in want}
    print(json.dumps(snap, indent=2, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(_main())
