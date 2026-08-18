"""
Physical memory, swap, and the hugepage and THP breakdown.
"""

import time

from ..util import rf


# ═══════════════════════════════════════════════════════════════════════════════
#  Memory
# ═══════════════════════════════════════════════════════════════════════════════
class MemCollector:
    KEYS = ('pgfault', 'pgmajfault', 'pswpin', 'pswpout')

    def __init__(self):
        self._prev = {}
        self._t = time.monotonic()

    def read(self):
        m = {}
        for line in rf('/proc/meminfo').split('\n'):
            p = line.split()
            if len(p) >= 2:
                m[p[0].rstrip(':')] = int(p[1])          # kB

        total = m.get('MemTotal', 1)
        free  = m.get('MemFree', 0)
        buf   = m.get('Buffers', 0)
        cache = m.get('Cached', 0) + m.get('SReclaimable', 0) - m.get('Shmem', 0)
        used  = total - free - buf - cache                # htop definition
        swt   = m.get('SwapTotal', 0)
        swf   = m.get('SwapFree', 0)

        now = time.monotonic()
        dt = max(now - self._t, 0.001)
        self._t = now
        vm, rates = {}, {}
        for line in rf('/proc/vmstat').split('\n'):
            p = line.split()
            if len(p) == 2:
                vm[p[0]] = int(p[1])
        for k in self.KEYS:
            cur = vm.get(k, 0)
            rates[k] = max(cur - self._prev.get(k, cur), 0) / dt
            self._prev[k] = cur

        hp_total = m.get('HugePages_Total', 0)
        hp_free  = m.get('HugePages_Free', 0)
        hp_sz    = m.get('Hugepagesize', 2048)

        return {
            'total_gb': total / 1_048_576,
            'used_gb':  used / 1_048_576,
            'buf_gb':   buf / 1_048_576,
            'cache_gb': max(cache, 0) / 1_048_576,
            'free_gb':  free / 1_048_576,
            'used_pct': used / total * 100,
            'swap_total_gb': swt / 1_048_576,
            'swap_used_gb': (swt - swf) / 1_048_576,
            'swap_pct': (swt - swf) / max(swt, 1) * 100,
            'dirty_mb': m.get('Dirty', 0) / 1024,
            'writeback_mb': m.get('Writeback', 0) / 1024,
            'hp_total_gb': hp_total * hp_sz / 1_048_576,
            'hp_used_gb': (hp_total - hp_free) * hp_sz / 1_048_576,
            'pgfault_s': rates['pgfault'],
            'pgmajfault_s': rates['pgmajfault'],
            'swapin_s': rates['pswpin'],
            'swapout_s': rates['pswpout'],
        }
