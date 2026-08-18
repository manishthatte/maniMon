"""
CPU utilisation, frequency, temperature, PSI pressure and NUMA topology.
"""

import os, re, glob, time

from ..util import rf, ri, sh, read_temps, hwmon_by_name


# ═══════════════════════════════════════════════════════════════════════════════
#  CPU
# ═══════════════════════════════════════════════════════════════════════════════
class CPUCollector:
    """Per-thread utilization, topology, frequency, temperature, pressure."""

    # AMD Zen exposes package energy through the intel_rapl_msr driver
    # (MSR_AMD_PKG_ENERGY_STAT), so this is genuine EPYC package power despite
    # the "intel-rapl" name. energy_uj is root-only by default since
    # CVE-2020-8694 (PLATYPUS); install_system_sensors.sh can relax that.
    RAPL_GLOB = '/sys/class/powercap/intel-rapl:[0-9]*'

    def __init__(self):
        self._prev = {}
        self._prev_stat = {}
        self._prev_t = time.monotonic()
        self.topo = self._topology()
        self.ncpu = len(self.topo['cpus'])
        self._rapl = self._find_rapl()
        self._rapl_prev = None
        self._rapl_t = time.monotonic()

    def _find_rapl(self):
        """Package-level RAPL domains that we can actually read."""
        out = []
        for d in sorted(glob.glob(self.RAPL_GLOB)):
            if not rf(f'{d}/name').startswith('package'):
                continue
            try:
                with open(f'{d}/energy_uj') as fh:
                    fh.read()
            except Exception:
                continue                      # unreadable — stays unreported
            out.append(d)
        return out

    def package_power(self):
        """
        Watts, from the derivative of the RAPL energy counter.
        Returns None when the counter is unreadable, rather than a fake zero.
        """
        if not self._rapl:
            # cheap re-probe: permissions may be granted while we run
            self._rapl = self._find_rapl()
            if not self._rapl:
                return None
        now = time.monotonic()
        total = 0
        for d in self._rapl:
            total += ri(f'{d}/energy_uj', 0)
        dt = now - self._rapl_t
        prev = self._rapl_prev
        self._rapl_prev, self._rapl_t = total, now
        if prev is None or dt <= 0:
            return None
        delta = total - prev
        if delta < 0:                          # counter wrapped
            wrap = sum(ri(f'{d}/max_energy_range_uj', 0) for d in self._rapl)
            delta += wrap
        watts = delta / 1e6 / dt
        return round(watts, 1) if 0 <= watts < 2000 else None

    _CEILING = None

    @classmethod
    def freq_ceiling(cls):
        """
        The part's true maximum clock in MHz, cached.

        cpuinfo_max_freq carries the boost ceiling even when the governor's
        scaling_max_freq does not; lscpu's "CPU max MHz" is the fallback, and
        the observed maximum is the last resort so this can never return zero.
        """
        if cls._CEILING:
            return cls._CEILING
        khz = max((ri(f, 0) for f in glob.glob(
            '/sys/devices/system/cpu/cpu[0-9]*/cpufreq/cpuinfo_max_freq')),
            default=0)
        if khz:
            cls._CEILING = khz / 1000.0
            return cls._CEILING
        m = re.search(r'CPU max MHz:\s*([\d.]+)', sh('lscpu', 2))
        cls._CEILING = float(m.group(1)) if m else 3000.0
        return cls._CEILING

    @staticmethod
    def _topology():
        cpus, cores = [], {}
        for p in glob.glob('/sys/devices/system/cpu/cpu[0-9]*'):
            n = os.path.basename(p)[3:]
            if not n.isdigit():
                continue
            cid = ri(f'{p}/topology/core_id', -1)
            cpus.append(int(n))
            cores.setdefault(cid, []).append(int(n))
        cpus.sort()
        for v in cores.values():
            v.sort()
        return {'cpus': cpus, 'cores': cores}

    def heat_order(self):
        """
        CPU indices ordered so SMT siblings sit adjacent.
        Core k occupies cells 2k, 2k+1 — a busy core reads as a 2-cell block.
        """
        order = []
        for cid in sorted(self.topo['cores']):
            order.extend(self.topo['cores'][cid])
        return order

    def read(self):
        now = time.monotonic()
        dt = max(now - self._prev_t, 0.001)
        self._prev_t = now

        per, agg = {}, {}
        ctxt = forks = procs_running = procs_blocked = 0

        for line in rf('/proc/stat').split('\n'):
            p = line.split()
            if not p:
                continue
            if p[0].startswith('cpu'):
                v = [int(x) for x in p[1:]]
                total = sum(v)
                idle = v[3] + (v[4] if len(v) > 4 else 0)
                prev = self._prev.get(p[0])
                if prev:
                    dtot = max(total - prev[0], 1)
                    busy = round((1 - (idle - prev[1]) / dtot) * 100, 1)
                    if p[0] == 'cpu':
                        agg = {
                            'total': max(0.0, busy),
                            'user':  round((v[0] - prev[2]) / dtot * 100, 1),
                            'sys':   round((v[2] - prev[3]) / dtot * 100, 1),
                            'iowait': round(((v[4] if len(v) > 4 else 0) - prev[4]) / dtot * 100, 1),
                        }
                    else:
                        per[int(p[0][3:])] = max(0.0, busy)
                self._prev[p[0]] = (total, idle, v[0], v[2],
                                    v[4] if len(v) > 4 else 0)
            elif p[0] == 'ctxt':
                ctxt = int(p[1])
            elif p[0] == 'processes':
                forks = int(p[1])
            elif p[0] == 'procs_running':
                procs_running = int(p[1])
            elif p[0] == 'procs_blocked':
                procs_blocked = int(p[1])

        ctxt_s = (ctxt - self._prev_stat.get('ctxt', ctxt)) / dt
        fork_s = (forks - self._prev_stat.get('forks', forks)) / dt
        self._prev_stat = {'ctxt': ctxt, 'forks': forks}

        # Frequency. Bin edges are derived from the part's real ceiling, not
        # hardcoded: under acpi-cpufreq (which this box reverted to when
        # amd_pstate was dropped) scaling_max_freq reports the 2700 MHz ACPI
        # P-state ceiling, not the 3911 MHz boost clock. Fixed 3200/2000 edges
        # against that put 63 of 64 threads in "low" at idle and would have
        # kept the boost bin near zero under load — a display that lies.
        freqs = []
        for f in glob.glob('/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq'):
            khz = ri(f, 0)
            if khz:
                freqs.append(khz / 1000.0)
        ceiling = self.freq_ceiling()
        boost_edge, mid_edge = ceiling * 0.82, ceiling * 0.51
        boost = sum(1 for m in freqs if m >= boost_edge)
        mid   = sum(1 for m in freqs if mid_edge <= m < boost_edge)
        low   = sum(1 for m in freqs if m < mid_edge)

        # Temperature — k10temp: Tctl plus per-CCD
        temps, tctl, ccd = {}, 0.0, []
        for hw in hwmon_by_name('k10temp') or hwmon_by_name('zenpower'):
            temps = read_temps(hw)
            tctl = temps.get('Tctl', 0.0)
            ccd = [v for k, v in sorted(temps.items()) if k.startswith('Tccd')]

        la = rf('/proc/loadavg').split()
        loadavg = [float(x) for x in la[:3]] if len(la) >= 3 else [0, 0, 0]

        return {
            'per_cpu': [per.get(i, 0.0) for i in range(self.ncpu)],
            'heat_order': self.heat_order(),
            'agg': agg or {'total': 0, 'user': 0, 'sys': 0, 'iowait': 0},
            'ncpu': self.ncpu,
            'ncore': len(self.topo['cores']),
            'freq_avg': round(sum(freqs) / len(freqs), 0) if freqs else 0,
            # freq_max is now the observed peak across threads; freq_ceiling is
            # what the silicon can do. Conflating the two was the bug.
            'freq_max': round(max(freqs), 0) if freqs else 0,
            'freq_ceiling': round(ceiling, 0),
            'freq_bins': {'boost': boost, 'mid': mid, 'low': low},
            'freq_driver': rf('/sys/devices/system/cpu/cpu0/cpufreq/scaling_driver'),
            'governor': rf('/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor'),
            'temp': tctl,
            'temp_ccd': ccd,
            'ctxt_s': round(ctxt_s, 0),
            'fork_s': round(fork_s, 1),
            'runq': procs_running,
            'blocked': procs_blocked,
            'loadavg': loadavg,
            # k10temp exposes no power1_average on this part, so package power
            # comes from RAPL. None (not 0) when the counter is unreadable.
            'power': self.package_power(),
        }


def pressure():
    """PSI — the single best 'is this box struggling' signal."""
    out = {}
    for res in ('cpu', 'io', 'memory'):
        d = {}
        for line in rf(f'/proc/pressure/{res}').split('\n'):
            p = line.split()
            if not p:
                continue
            kind = p[0]                       # some | full
            for kv in p[1:]:
                if '=' in kv:
                    k, v = kv.split('=', 1)
                    if k.startswith('avg'):
                        d[f'{kind}_{k}'] = float(v)
        out[res] = d
    return out


def numa_nodes(per_cpu):
    """Per-NUMA-node memory + CPU. Returns [] semantics preserved for 1 node."""
    nodes = []
    for path in sorted(glob.glob('/sys/devices/system/node/node[0-9]*')):
        nid = os.path.basename(path)[4:]
        total_kb = free_kb = 0
        for line in rf(f'{path}/meminfo').split('\n'):
            p = line.split()
            if len(p) >= 4:
                if 'MemTotal' in line: total_kb = int(p[3])
                if 'MemFree'  in line: free_kb = int(p[3])
        cpus = _parse_cpulist(rf(f'{path}/cpulist'))
        vals = [per_cpu[c] for c in cpus if c < len(per_cpu)]
        nodes.append({
            'id': nid,
            'cpulist': rf(f'{path}/cpulist'),
            'used_gb': (total_kb - free_kb) / 1_048_576,
            'total_gb': total_kb / 1_048_576,
            'mem_pct': (total_kb - free_kb) / max(total_kb, 1) * 100,
            'cpu_pct': sum(vals) / len(vals) if vals else 0.0,
        })
    return nodes


def _parse_cpulist(s):
    cpus = []
    for part in s.strip().split(','):
        if '-' in part:
            a, b = part.split('-')
            cpus.extend(range(int(a), int(b) + 1))
        elif part.isdigit():
            cpus.append(int(part))
    return cpus
