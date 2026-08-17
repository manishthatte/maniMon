#!/usr/bin/env python3
"""
maniMon — data collection layer.

Pure data readers. NO GTK, NO rendering, NO X server required.
Run standalone to dump the whole machine state as JSON:

    python3 collectors.py --dump
    python3 collectors.py --dump cpu gpu mem

Every collector returns a plain dict/list of primitives so the output is
JSON-serialisable and can be consumed by any front end.

Author: Manish Jagdish Thatte
"""

import os, re, sys, json, glob, time, socket, subprocess

# ── Paths / config ────────────────────────────────────────────────────────────
# Everything site-specific comes from config.py. Nothing in this file names a
# particular machine, user or project — see config.SAMPLE for what can be set.
from config import (STATE_DIR, JOB_ROOTS, JOB_BINS, MPI_LAUNCHERS, JOB_ID_RE,
                    SERVICES, REPO_PATH, BACKUP_DIR, BACKUP_JOBS,
                    CAMPAIGN_ROOT, LAYERS, VENVS, LIMITS, SENSOR_DIR)

# Backwards-compatible aliases used throughout this module.
SIM_ROOTS = JOB_ROOTS
SIM_BINS = JOB_BINS
SIM_ID_RE = JOB_ID_RE
REPO = REPO_PATH
PHASE3 = CAMPAIGN_ROOT
TOOLS = BACKUP_DIR

HZ         = os.sysconf('SC_CLK_TCK') or 100
PAGE       = os.sysconf('SC_PAGE_SIZE') or 4096

# SIM_BINS, MPI_LAUNCHERS and BACKUP_JOBS all come from config.py — see the
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


def _hwmon_by_name(name):
    """All hwmon directories whose 'name' file matches."""
    out = []
    for h in sorted(glob.glob('/sys/class/hwmon/hwmon*')):
        if rf(f'{h}/name') == name:
            out.append(h)
    return out


def _temps(hw):
    """{label: celsius} for one hwmon directory."""
    res = {}
    for tin in sorted(glob.glob(f'{hw}/temp*_input')):
        idx = os.path.basename(tin).replace('temp', '').replace('_input', '')
        lbl = rf(f'{hw}/temp{idx}_label') or f'temp{idx}'
        val = ri(tin, 0)
        if val:
            res[lbl] = round(val / 1000.0, 1)
    return res


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
        for hw in _hwmon_by_name('k10temp') or _hwmon_by_name('zenpower'):
            temps = _temps(hw)
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


# ═══════════════════════════════════════════════════════════════════════════════
#  GPU
# ═══════════════════════════════════════════════════════════════════════════════
def gpus():
    """
    Enumerate real compute GPUs.

    NOTE: card0 on this machine is the ASPEED BMC VGA, card1 is the W7900.
    Filtering on gpu_busy_percent is what separates them. Count is dynamic —
    one card or two both render correctly.
    """
    out = []
    for card in sorted(glob.glob('/sys/class/drm/card[0-9]')):
        dev = f'{card}/device'
        if not os.path.exists(f'{dev}/gpu_busy_percent'):
            continue
        hw = next(iter(glob.glob(f'{dev}/hwmon/hwmon*')), None)
        t = _temps(hw) if hw else {}
        vused = ri(f'{dev}/mem_info_vram_used', 0)
        vtot  = ri(f'{dev}/mem_info_vram_total', 1)
        power = ri(f'{hw}/power1_average', 0) / 1e6 if hw else 0.0
        cap   = ri(f'{hw}/power1_cap', 0) / 1e6 if hw else 0.0
        fan   = ri(f'{hw}/fan1_input', 0) if hw else 0
        fanmx = ri(f'{hw}/fan1_max', 0) if hw else 0
        sclk  = ri(f'{hw}/freq1_input', 0) // 1_000_000 if hw else 0
        mclk  = ri(f'{hw}/freq2_input', 0) // 1_000_000 if hw else 0
        busy  = ri(f'{dev}/gpu_busy_percent', 0)
        membusy = ri(f'{dev}/mem_busy_percent', 0)
        out.append({
            'card': os.path.basename(card),
            'name': rf(f'{dev}/product_name') or 'Radeon Pro W7900',
            'busy': busy,
            'mem_busy': membusy,
            'vram_used_gb': vused / 1_073_741_824,
            'vram_total_gb': vtot / 1_073_741_824,
            'vram_pct': vused / max(vtot, 1) * 100,
            'temp_edge': t.get('edge', 0.0),
            'temp_junction': t.get('junction', 0.0),
            'temp_mem': t.get('mem', 0.0),
            'power': round(power, 1),
            'power_cap': round(cap, 1),
            'fan_rpm': fan,
            'fan_pct': round(fan / fanmx * 100, 0) if fanmx else 0,
            'sclk': sclk,
            'mclk': mclk,
            'link_speed': rf(f'{dev}/current_link_speed', '?'),
            'link_width': rf(f'{dev}/current_link_width', '?'),
            'link_speed_max': rf(f'{dev}/max_link_speed', '?'),
            'link_width_max': rf(f'{dev}/max_link_width', '?'),
            # estimates against W7900 peak figures
            'tflops': round(busy / 100 * 61.3, 1),
            'bw_gbs': round(membusy / 100 * 864, 0),
        })
    return out


def gpu_clients():
    """
    PIDs holding a render node open — i.e. what is actually using the GPU.
    Cheap enough for the SLOW tier; only our own processes are visible.
    """
    pids = {}
    for fd in glob.glob('/proc/[0-9]*/fd/*'):
        try:
            tgt = os.readlink(fd)
        except Exception:
            continue
        if '/dev/dri/renderD' in tgt or '/dev/dri/card' in tgt:
            pid = fd.split('/')[2]
            if pid not in pids:
                comm = rf(f'/proc/{pid}/comm')
                if comm:
                    pids[pid] = comm
    return pids


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


# ═══════════════════════════════════════════════════════════════════════════════
#  Storage — devices, partitions, IO, temperature
# ═══════════════════════════════════════════════════════════════════════════════
def _whole_disk(name):
    """Partition or disk name -> the whole-disk name it belongs to."""
    if os.path.exists(f'/sys/block/{name}'):
        return name                                  # already a whole disk
    if name.startswith('nvme'):
        return re.sub(r'p\d+$', '', name)
    return re.sub(r'\d+$', '', name)


def _base_dev(devnode):
    """
    Mount source -> the PHYSICAL device it ultimately lives on.

        /dev/nvme0n1p3            -> nvme0n1
        /dev/sda                  -> sda
        /dev/mapper/debian--vg-root -> nvme0n1   (via dm-0 -> nvme0n1p3)

    The device-mapper case is why this exists. The old implementation just
    stripped trailing digits, so an LVM root came out as the mapper name
    itself. After the 17 Aug reinstall put /, /var and /tmp on LVM that
    produced three phantom "devices" of size 0 with no temperature and no I/O,
    while the NVMe appeared to hold nothing but /boot — 1.1 GB of a 1.7 TB
    disk. Everything on LVM was effectively invisible.
    """
    real = os.path.realpath(devnode)
    n = os.path.basename(real)
    if n.startswith('dm-'):
        # A dm device's slaves are its backing partitions. One hop is enough
        # for plain LVM; walk any further stacking (dm on dm) as it appears.
        seen = set()
        while n.startswith('dm-') and n not in seen:
            seen.add(n)
            slaves = sorted(glob.glob(f'/sys/block/{n}/slaves/*'))
            if not slaves:
                return n                             # orphan: report as itself
            n = os.path.basename(slaves[0])
        return _whole_disk(n)
    return _whole_disk(n)


def _dm_for(devnode):
    """The dm-N name behind a mount source, or None if it is not mapped."""
    n = os.path.basename(os.path.realpath(devnode))
    return n if n.startswith('dm-') else None


def _disk_labels():
    out = {}
    for link in glob.glob('/dev/disk/by-label/*'):
        try:
            out[os.path.basename(os.path.realpath(link))] = os.path.basename(link)
        except Exception:
            pass
    return out


def _drive_temps():
    """
    {block_device: celsius}. NVMe always available; SATA/HDD only when the
    `drivetemp` module is loaded (sudo modprobe drivetemp).
    """
    out = {}
    for hw in _hwmon_by_name('nvme'):
        t = _temps(hw)
        val = t.get('Composite') or (list(t.values())[0] if t else None)
        real = os.path.realpath(f'{hw}/device')
        blk = glob.glob(f'{real}/nvme/nvme*/nvme*n[0-9]') or \
              glob.glob(f'{os.path.dirname(real)}/nvme/nvme*/nvme*n[0-9]')
        if val:
            if blk:
                out[os.path.basename(blk[0])] = val
            else:
                out.setdefault('nvme0n1', val)
    for hw in _hwmon_by_name('drivetemp'):
        t = _temps(hw)
        val = list(t.values())[0] if t else None
        blk = glob.glob(f'{os.path.realpath(f"{hw}/device")}/block/*')
        if val and blk:
            out[os.path.basename(blk[0])] = val
    return out


# ── health.py bridges ─────────────────────────────────────────────────────────
# health.py is imported lazily and each call is wrapped, so a monitor that has
# never had sensord deployed still starts and simply shows those sections as
# unavailable.
def _health_call(fn, default):
    try:
        import health
        return getattr(health, fn)()
    except Exception:
        return default


def _lvm_volumes():   return _health_call('lvm', [])
def _bmc():           return _health_call('bmc', {'present': False})
def _smart():         return _health_call('smart', {})
def _dimms():         return _health_call('dimms', {'present': False})
def _ecc():           return _health_call('ecc', {'present': False})
def _peripherals():   return _health_call('peripherals', [])


def _gpu_metrics():
    """Keyed by card so it lines up with the `gpus` list."""
    return _health_call('gpu_metrics', {})


class DiskCollector:
    def __init__(self):
        self._prev = {}
        self._t = time.monotonic()
        self._mounts_cache = ([], 0.0)

    def io(self):
        """Per-device IO rates, queue depth and await."""
        now = time.monotonic()
        dt = max(now - self._t, 0.001)
        self._t = now
        res = {}
        for line in rf('/proc/diskstats').split('\n'):
            p = line.split()
            if len(p) < 14:
                continue
            dev = p[2]
            # dm-N included: with / and /var on LVM, the physical-device rows
            # in /proc/diskstats do see the traffic, but per-volume I/O is only
            # visible on the mapper devices.
            if not re.fullmatch(r'(sd[a-z]|nvme\d+n\d+|dm-\d+)', dev):
                continue
            r_io, r_sec, r_ms = int(p[3]), int(p[5]), int(p[6])
            w_io, w_sec, w_ms = int(p[7]), int(p[9]), int(p[10])
            queue = int(p[11])
            prev = self._prev.get(dev)
            if prev:
                dr, dw = max(r_io - prev[0], 0), max(w_io - prev[3], 0)
                res[dev] = {
                    'r_bps': max(r_sec - prev[1], 0) * 512 / dt,
                    'w_bps': max(w_sec - prev[4], 0) * 512 / dt,
                    'r_iops': dr / dt,
                    'w_iops': dw / dt,
                    'r_await': max(r_ms - prev[2], 0) / max(dr, 1),
                    'w_await': max(w_ms - prev[5], 0) / max(dw, 1),
                    'queue': queue,
                }
            self._prev[dev] = (r_io, r_sec, r_ms, w_io, w_sec, w_ms)
        return res

    def mounts(self, max_age=8.0):
        """Every real filesystem, grouped by physical device. Cached."""
        cached, ts = self._mounts_cache
        if cached and time.monotonic() - ts < max_age:
            return cached

        labels = _disk_labels()
        temps = _drive_temps()
        devices = {}

        for line in rf('/proc/mounts').split('\n'):
            p = line.split()
            if len(p) < 3 or not p[0].startswith('/dev/'):
                continue
            src, mnt, fstype = p[0], p[1].replace('\\040', ' '), p[2]
            if fstype in ('squashfs', 'iso9660', 'devtmpfs'):
                continue
            base = _base_dev(src)
            try:
                st = os.statvfs(mnt)
            except Exception:
                continue
            total = st.f_blocks * st.f_frsize
            avail = st.f_bavail * st.f_frsize
            used = total - st.f_bfree * st.f_frsize
            if total == 0:
                continue
            d = devices.setdefault(base, {
                'dev': base,
                'size': ri(f'/sys/block/{base}/size', 0) * 512,
                'rotational': ri(f'/sys/block/{base}/queue/rotational', 0) == 1,
                'usb': 'usb' in os.path.realpath(f'/sys/block/{base}/device'),
                'model': rf(f'/sys/block/{base}/device/model') or
                         rf(f'/sys/block/{base}/device/model', ''),
                'temp': temps.get(base),
                'parts': [],
            })
            d['parts'].append({
                'src': src,
                'mount': mnt,
                'label': labels.get(os.path.basename(src), ''),
                'fstype': fstype,
                'total': total,
                'used': used,
                'avail': avail,
                'pct': used / total * 100,
                'dm': _dm_for(src),          # so per-volume I/O can be shown
            })

        # swap partitions belong in the picture too
        for line in rf('/proc/swaps').split('\n')[1:]:
            p = line.split()
            if len(p) >= 4 and p[0].startswith('/dev/'):
                base = _base_dev(p[0])
                total, used = int(p[2]) * 1024, int(p[3]) * 1024
                if base in devices:
                    devices[base]['parts'].append({
                        'src': p[0], 'mount': '[swap]', 'label': '',
                        'fstype': 'swap', 'total': total, 'used': used,
                        'avail': total - used, 'pct': used / max(total, 1) * 100,
                        'dm': _dm_for(p[0]),
                    })

        # Allocated-but-unmounted logical volumes. Nothing that walks
        # /proc/mounts can see these, so the 1.5 TB debian--vg-home LV left
        # behind by the reinstall was simply absent from the panel and the NVMe
        # looked far emptier than it is.
        for vg in _lvm_volumes():
            for lv in vg['lvs']:
                if not lv['unused']:
                    continue
                base = _base_dev(f"/dev/{lv['dm']}")
                if base in devices:
                    devices[base]['parts'].append({
                        'src': f"/dev/mapper/{vg['vg']}-{lv['lv']}",
                        'mount': f"[unused lv: {lv['lv']}]",
                        'label': '', 'fstype': 'lvm',
                        'total': lv['size'], 'used': lv['size'],
                        'avail': 0, 'pct': 100.0,
                        'dm': lv['dm'], 'unused_lv': True,
                    })

        out = sorted(devices.values(),
                     key=lambda d: (d['usb'], d['dev']))
        for d in out:
            d['parts'].sort(key=lambda p: p['mount'])
        self._mounts_cache = (out, time.monotonic())
        return out


# ═══════════════════════════════════════════════════════════════════════════════
#  Network
# ═══════════════════════════════════════════════════════════════════════════════
def _iface_kind(ifc, path):
    """
    Classify an interface: 'lan', 'bmc', 'virtual' or 'wireless'.

    The BMC's USB Ethernet (AMI 046b:ffb0) was being drawn as a peer of the
    real 10G NIC — no IP, permanently carrier-up, 397 drops. It is also the
    exact interface behind the 14 Aug silent-LAN-drop bug, where NetworkManager
    generated a profile for it and the machine lost its address for 4 h 40 m of
    a 14.5 h uptime. It deserves a label, not equal billing.
    """
    if os.path.exists(f'{path}/wireless') or os.path.exists(f'{path}/phy80211'):
        return 'wireless'
    real = os.path.realpath(f'{path}/device') if os.path.exists(f'{path}/device') else ''
    if not real:
        return 'virtual'
    vendor = rf(f'{path}/device/../idVendor') or rf(f'{path}/device/idVendor')
    product = rf(f'{path}/device/../idProduct') or rf(f'{path}/device/idProduct')
    if (vendor, product) == ('046b', 'ffb0'):        # AMI virtual Ethernet
        return 'bmc'
    # Fallback: a USB NIC whose MAC is locally administered and which never
    # carries an address is a management interface in all but name.
    if 'usb' in real and ifc.startswith('enx'):
        return 'bmc'
    return 'lan'


class NetCollector:
    def __init__(self):
        self._prev = {}
        self._t = time.monotonic()
        self._addr_cache = ({}, 0.0)
        self._wan_cache = (None, 0.0)

    @staticmethod
    def _nic_temps():
        """Map interface name -> NIC temperature via shared PCI device."""
        out = {}
        for hw in glob.glob('/sys/class/hwmon/hwmon*'):
            t = _temps(hw)
            if not t:
                continue
            real = os.path.realpath(f'{hw}/device')
            for nic in glob.glob(f'{real}/net/*'):
                out[os.path.basename(nic)] = list(t.values())[0]
        return out

    def addresses(self, max_age=30.0):
        cached, ts = self._addr_cache
        if cached and time.monotonic() - ts < max_age:
            return cached
        out = {}
        for fam, flag in (('v4', '-4'), ('v6', '-6')):
            for line in sh(f'ip -o {flag} addr show', timeout=2).split('\n'):
                p = line.split()
                if len(p) >= 4 and p[3] != 'scope':
                    ifc, addr = p[1], p[3]
                    if fam == 'v6' and addr.startswith('fe80'):
                        continue
                    out.setdefault(ifc, {}).setdefault(fam, addr)
        self._addr_cache = (out, time.monotonic())
        return out

    def wan(self, max_age=30.0):
        """One cheap ping. Link speed alone is misleading — WAN is the real limit."""
        cached, ts = self._wan_cache
        if cached is not None and time.monotonic() - ts < max_age:
            return cached
        out = sh('ping -c1 -W2 -n 1.1.1.1', timeout=4)
        m = re.search(r'time=([\d.]+)\s*ms', out)
        res = {'up': bool(m), 'ms': float(m.group(1)) if m else None}
        self._wan_cache = (res, time.monotonic())
        return res

    def read(self):
        now = time.monotonic()
        dt = max(now - self._t, 0.001)
        self._t = now
        temps = self._nic_temps()
        addrs = self.addresses()
        out = []
        for path in sorted(glob.glob('/sys/class/net/*')):
            ifc = os.path.basename(path)
            if ifc == 'lo':
                continue
            state = rf(f'{path}/operstate', 'unknown')
            rx = ri(f'{path}/statistics/rx_bytes')
            tx = ri(f'{path}/statistics/tx_bytes')
            prev = self._prev.get(ifc)
            rx_bps = tx_bps = 0.0
            if prev:
                rx_bps = max(rx - prev[0], 0) / dt
                tx_bps = max(tx - prev[1], 0) / dt
            speed = ri(f'{path}/speed', 0)
            up = state == 'up'
            errors = (ri(f'{path}/statistics/rx_errors') +
                      ri(f'{path}/statistics/tx_errors'))
            dropped = (ri(f'{path}/statistics/rx_dropped') +
                       ri(f'{path}/statistics/tx_dropped'))
            # Rates, not just since-boot totals. A cumulative 11924 drops says
            # nothing about whether drops are happening NOW, which is the only
            # question a live panel can usefully answer.
            err_s = drop_s = 0.0
            if prev and len(prev) >= 4:
                err_s = max(errors - prev[2], 0) / dt
                drop_s = max(dropped - prev[3], 0) / dt
            self._prev[ifc] = (rx, tx, errors, dropped)

            out.append({
                'iface': ifc,
                'state': state,
                'up': up,
                'kind': _iface_kind(ifc, path),
                'speed_mbps': speed if speed > 0 else 0,
                'mac': rf(f'{path}/address'),
                'ipv4': addrs.get(ifc, {}).get('v4', ''),
                'ipv6': addrs.get(ifc, {}).get('v6', ''),
                'rx_bps': rx_bps,
                'tx_bps': tx_bps,
                'rx_total': rx,
                'tx_total': tx,
                'errors': errors,
                'dropped': dropped,
                'err_s': err_s,
                'drop_s': drop_s,
                # A NIC with no carrier still reports its die temperature, but
                # showing 60 C beside a dead link reads as a live measurement
                # of something that is not running.
                'temp': temps.get(ifc) if up else None,
            })
        # Real interfaces first, then management, then anything down.
        out.sort(key=lambda n: (n['kind'] != 'lan', not n['up'], n['iface']))
        return out


def sockets():
    tcp = udp = 0
    for line in rf('/proc/net/sockstat').split('\n'):
        if line.startswith('TCP:'):
            m = re.search(r'inuse (\d+)', line)
            tcp = int(m.group(1)) if m else 0
        elif line.startswith('UDP:'):
            m = re.search(r'inuse (\d+)', line)
            udp = int(m.group(1)) if m else 0
    listening = 0
    for f in ('/proc/net/tcp', '/proc/net/tcp6'):
        for line in rf(f).split('\n')[1:]:
            p = line.split()
            if len(p) > 3 and p[3] == '0A':
                listening += 1
    return {'tcp': tcp, 'udp': udp, 'listening': listening}


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
# SIM_ID_RE comes from config (jobs.id_regex); when unset, a job is named
# after its executable instead of a pattern in its command line.
OUT_EXT = ('.out', '.log', '.txt', '.err', '.dat', '.stdout')


def _sim_id(cmdline):
    """A short display name for a job, from its command line.

    Returns None when no id_regex is configured — the caller then falls back to
    the executable name, which is always available.
    """
    if SIM_ID_RE is None:
        return None
    for tok in cmdline.split():
        m = SIM_ID_RE.search(os.path.basename(tok))
        if m:
            return m.group(1).replace('_', '-') if tok else None
    m = SIM_ID_RE.search(cmdline)
    return m.group(1) if m else None


def is_sim(proc):
    """True when the process is real physics work rather than desktop noise."""
    comm, cmd = proc['comm'], proc['cmdline']
    if comm in SIM_BINS or comm in MPI_LAUNCHERS:
        return True
    if comm.startswith(('python', 'lmp', 'pw.', 'gpaw')):
        if any(root in cmd for root in SIM_ROOTS):
            return True
        if SIM_ID_RE is not None and SIM_ID_RE.search(cmd):
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


# ═══════════════════════════════════════════════════════════════════════════════
#  tmux — sessions, windows, panes, live output
# ═══════════════════════════════════════════════════════════════════════════════
class TmuxCollector:
    FMT = ('#{session_name}\t#{window_index}\t#{window_name}\t#{pane_id}\t'
           '#{pane_pid}\t#{pane_current_command}\t#{pane_dead}\t'
           '#{window_active}\t#{session_attached}')

    def __init__(self):
        self._cap = {}

    def read(self, capture=True):
        raw = sh(f'tmux list-panes -a -F "{self.FMT}" 2>/dev/null', timeout=2)
        if not raw:
            return []
        panes = []
        for line in raw.split('\n'):
            f = line.split('\t')
            if len(f) < 9:
                continue
            pane_id = f[3]
            last = ''
            if capture:
                last = self._capture(pane_id)
            panes.append({
                'session': f[0],
                'window': f[1],
                'window_name': f[2],
                'pane': pane_id,
                'pid': int(f[4]) if f[4].isdigit() else 0,
                'cmd': f[5],
                'dead': f[6] == '1',
                'active': f[7] == '1',
                'attached': f[8] not in ('', '0'),
                'last_line': last,
            })
        return panes

    def _capture(self, pane_id, max_age=4.0):
        val, ts = self._cap.get(pane_id, ('', 0.0))
        if val and time.monotonic() - ts < max_age:
            return val
        out = sh(f'tmux capture-pane -p -t {pane_id} -S -6 2>/dev/null', timeout=2)
        line = ''
        for ln in reversed(out.split('\n')):
            if ln.strip():
                line = ln.strip()[:60]
                break
        self._cap[pane_id] = (line, time.monotonic())
        return line


# ═══════════════════════════════════════════════════════════════════════════════
#  Campaign — an optional long-running study
# ═══════════════════════════════════════════════════════════════════════════════
# LAYERS comes from config (campaign.layers). Empty by default, which hides
# the campaign section rather than showing an empty scoreboard.


def campaign():
    """
    Progress of a long-running study, when one is configured.

    Source-of-truth policy:

      STATUS.md is authoritative for the SCOREBOARD. confirmed / partial /
      pending are human judgements about whether a result actually validates a
      claim; no file on disk records that, so the filesystem cannot supply it.

      The filesystem is authoritative for EXECUTION. Whether a sim has run, when,
      and with what exit code comes from the output directory and
      .runner_status.json, which are generated and cannot drift.

    These answer different questions, so subtracting one from the other would be
    a category error — a sim can have run and still not be confirmed. What IS a
    genuine inconsistency, and is therefore flagged, is a layer the ledger calls
    confirmed that has no output artefacts at all: a claim with nothing behind it.
    """
    ledger = {}
    totals = {'scripts': 0, 'confirmed': 0, 'partial': 0, 'pending': 0}
    if not PHASE3:                      # no campaign configured: hide the section
        return {}
    status_md = f'{PHASE3}/STATUS.md'
    num = r'\s*\**(\d+)\**\s*'
    row = re.compile(r'^\|\s*\**(L\d)\**[^|]*\|' + num + r'\|' + num +
                     r'\|' + num + r'\|' + num + r'\|')
    tot = re.compile(r'^\|\s*\**Total\**\s*\|' + num + r'\|' + num +
                     r'\|' + num + r'\|' + num + r'\|', re.I)
    for line in rf(status_md).split('\n'):
        line = line.strip()
        m = row.match(line)
        if m:
            g = [int(x) for x in m.groups()[1:]]
            ledger[m.group(1)] = {'scripts': g[0], 'confirmed': g[1],
                                  'partial': g[2], 'pending': g[3]}
            continue
        m = tot.match(line)
        if m:
            g = [int(x) for x in m.groups()]
            totals = {'scripts': g[0], 'confirmed': g[1],
                      'partial': g[2], 'pending': g[3]}

    # Execution evidence from the output tree
    ran, failed, failed_ids = {}, 0, []
    for d in sorted(glob.glob(f'{PHASE3}/output/*')):
        if not os.path.isdir(d):
            continue
        sim = os.path.basename(d)
        lay = sim[:2].upper().replace('_', '')
        # Any real output file counts: engines emit .dat/.json/.log/.png/.csv
        artefacts = [f for f in glob.glob(f'{d}/*') if os.path.isfile(f)]
        rs = f'{d}/.runner_status.json'
        rc = None
        if os.path.exists(rs):
            try:
                rc = json.load(open(rs)).get('returncode')
            except Exception:
                rc = None
        if rc not in (None, 0):
            failed += 1
            failed_ids.append(sim)
        if artefacts:
            ran[lay] = ran.get(lay, 0) + 1

    out = []
    for lay in LAYERS:
        led = ledger.get(lay, {'scripts': 0, 'confirmed': 0,
                               'partial': 0, 'pending': 0})
        out.append({
            'layer': lay,
            'scripts': led['scripts'],
            'confirmed': led['confirmed'],
            'partial': led['partial'],
            'pending': led['pending'],
            'ran': ran.get(lay, 0),
            # genuine inconsistency: ledger claims results, disk has nothing
            'unbacked': led['confirmed'] + led['partial'] > 0 and ran.get(lay, 0) == 0,
        })
    return {
        'layers': out,
        'scripts': totals['scripts'] or sum(l['scripts'] for l in out),
        'confirmed': totals['confirmed'] or sum(l['confirmed'] for l in out),
        'partial': totals['partial'] or sum(l['partial'] for l in out),
        'pending': totals['pending'] or sum(l['pending'] for l in out),
        'ran': sum(ran.values()),
        'failed': failed,
        'failed_ids': failed_ids[:5],
    }


def recent_results(hours=24, limit=10):
    """Jobs that produced output recently — the 'finished' feed."""
    if not PHASE3:                      # no campaign configured
        return []
    cutoff = time.time() - hours * 3600
    out = []
    for d in glob.glob(f'{PHASE3}/output/*'):
        if not os.path.isdir(d):
            continue
        sim = os.path.basename(d)
        rs, rc, secs = f'{d}/.runner_status.json', None, None
        if os.path.exists(rs):
            try:
                js = json.load(open(rs))
                rc, secs = js.get('returncode'), js.get('seconds')
            except Exception:
                pass
        newest, size = None, 0
        for f in glob.glob(f'{d}/*.json') + glob.glob(f'{d}/*.log'):
            try:
                m = os.path.getmtime(f)
            except Exception:
                continue
            if newest is None or m > newest:
                newest, size = m, os.path.getsize(f)
        if newest is None or newest < cutoff:
            continue
        out.append({
            'sim_id': sim,
            'mtime': newest,
            'age': time.time() - newest,
            'rc': rc,
            'seconds': secs,
            'size': size,
            'ok': rc in (0, None),
        })
    out.sort(key=lambda r: -r['mtime'])
    return out[:limit]


# ═══════════════════════════════════════════════════════════════════════════════
#  Repo, backups, services, journal
# ═══════════════════════════════════════════════════════════════════════════════
def repo():
    if not REPO:                        # no repo configured: hide the section
        return {}
    g = f'git -C {REPO}'
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
        path = os.path.join(TOOLS or '', logname)
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


# ═══════════════════════════════════════════════════════════════════════════════
#  Attention engine
# ═══════════════════════════════════════════════════════════════════════════════
SEV_CRIT, SEV_WARN, SEV_INFO, SEV_OK = 0, 1, 2, 3
STATE_FILE = f'{STATE_DIR}/seen.json'


def _load_state():
    try:
        with open(STATE_FILE) as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_state(state):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = STATE_FILE + '.tmp'
        with open(tmp, 'w') as fh:
            json.dump(state, fh)
        os.replace(tmp, STATE_FILE)
    except Exception:
        pass


def acknowledge(key):
    """Mark one item (or '*' for all currently known) as seen."""
    st = _load_state()
    st[key] = time.time()
    cutoff = time.time() - 7 * 86400
    _save_state({k: v for k, v in st.items() if v > cutoff})


def attention(snap):
    """
    Aggregate everything that might need Manish's eyes, ranked by severity.
    Items carry a stable `key` so a click can acknowledge them.
    """
    st = _load_state()
    items = []

    def add(key, sev, icon, text, ackable=True):
        if ackable and key in st:
            return
        items.append({'key': key, 'sev': sev, 'icon': icon,
                      'text': text, 'ackable': ackable})

    # Failed and finished simulations
    for r in snap.get('recent', []):
        stamp = int(r['mtime'])
        if r['rc'] not in (0, None):
            add(f'simfail:{r["sim_id"]}:{stamp}', SEV_CRIT, '✗',
                f'{r["sim_id"]} FAILED rc={r["rc"]} · {fmt_age(r["age"])}')
        else:
            dur = f' in {fmt_elapsed(r["seconds"])}' if r.get('seconds') else ''
            add(f'simdone:{r["sim_id"]}:{stamp}', SEV_WARN, '✓',
                f'{r["sim_id"]} finished{dur} · {fmt_age(r["age"])}')

    # Dead tmux panes
    for p in snap.get('tmux', []):
        if p['dead']:
            add(f'tmuxdead:{p["session"]}:{p["window"]}', SEV_CRIT, '▣',
                f'{p["session"]}:{p["window_name"]} pane exited')

    # Backups
    for b in snap.get('backups', []):
        if not b['mounted']:
            add(f'nomount:{b["label"]}', SEV_WARN, '⏏',
                f'{b["label"]} not mounted')
        elif b['stale']:
            add(f'stale:{b["label"]}', SEV_WARN, '⏏',
                f'{b["label"]} backup {fmt_age(b["age"])}')
        elif b['ok'] is False:
            add(f'bkpfail:{b["label"]}', SEV_CRIT, '⏏',
                f'{b["label"]} backup FAILED')

    # Filesystems filling up. An unmounted LV is synthesised at 100% so it
    # shows its full size on the storage bar — but it is not a filesystem
    # running out of room, and alarming on it would be a permanent false CRIT.
    for dev in snap.get('disks', []):
        for part in dev['parts']:
            if part.get('unused_lv'):
                continue
            if part['pct'] >= 92:
                add(f'full:{part["mount"]}', SEV_CRIT, '◫',
                    f'{part["mount"]} {part["pct"]:.0f}% full', ackable=False)
            elif part['pct'] >= 85:
                add(f'fill:{part["mount"]}', SEV_WARN, '◫',
                    f'{part["mount"]} {part["pct"]:.0f}% full', ackable=False)

    # Swap in use on a 251 GB box means something is wrong
    mem = snap.get('mem') or {}
    if mem.get('swap_used_gb', 0) > 1:
        add('swap', SEV_WARN, '▦',
            f'swap in use: {mem["swap_used_gb"]:.1f} GB', ackable=False)

    # Thermals
    for g in snap.get('gpus', []):
        if g['temp_junction'] >= 95:
            add(f'gputemp:{g["card"]}', SEV_CRIT, '◉',
                f'{g["card"]} junction {g["temp_junction"]:.0f}°C', ackable=False)

    # ── ECC: the earliest warning that a DIMM is failing ─────────────────────
    ec = snap.get('ecc') or {}
    if ec.get('present'):
        if ec.get('ue'):
            add(f'ecc_ue:{ec["ue"]}', SEV_CRIT, '▨',
                f'{ec["ue"]} UNCORRECTABLE ECC error(s) — memory is failing')
        elif ec.get('ce'):
            add(f'ecc_ce:{ec["ce"]}', SEV_WARN, '▨',
                f'{ec["ce"]} correctable ECC error(s)')

    # ── Drive health, from SMART ─────────────────────────────────────────────
    for dev, s in (snap.get('smart') or {}).items():
        if s.get('healthy') is False:
            add(f'smartfail:{dev}', SEV_CRIT, '◈',
                f'{dev} SMART health FAILED — replace it', ackable=False)
        if s.get('reallocated'):
            add(f'realloc:{dev}:{s["reallocated"]}', SEV_WARN, '◈',
                f'{dev} {s["reallocated"]} reallocated sector(s)')
        if s.get('pending'):
            add(f'pending:{dev}:{s["pending"]}', SEV_WARN, '◈',
                f'{dev} {s["pending"]} pending sector(s)')
        if s.get('media_errors'):
            add(f'mediaerr:{dev}:{s["media_errors"]}', SEV_WARN, '◈',
                f'{dev} {s["media_errors"]} media error(s)')
        life = s.get('life_pct')
        if isinstance(life, (int, float)) and life <= 10:
            add(f'life:{dev}', SEV_WARN, '◈',
                f'{dev} {life:.0f}% life remaining', ackable=False)
        # Seconds spent above the drive's own thermal limit. Non-zero is the
        # answer to "why was that run slower than the last one".
        if s.get('crit_temp_time'):
            add(f'nvmethrottle:{dev}', SEV_WARN, '◈',
                f'{dev} {s["crit_temp_time"]} min above critical temp', ackable=False)

    # ── Chassis: a fan at 0 while its neighbours spin is a dead fan ──────────
    bm = snap.get('bmc') or {}
    for fan in bm.get('dead_fans', []):
        add(f'deadfan:{fan}', SEV_CRIT, '❉', f'chassis fan {fan} reads 0 RPM',
            ackable=False)
    for rail in bm.get('rails_off_nominal', []):
        add(f'rail:{rail}', SEV_WARN, '⚡', f'{rail} more than 5% off nominal',
            ackable=False)

    # ── GPU throttling, only when the flag is trustworthy ───────────────────
    for card, gm in (snap.get('gpu_metrics') or {}).items():
        if gm.get('supported') and gm.get('throttled'):
            why = ', '.join(gm.get('throttle_reasons') or []) or 'unknown'
            add(f'throttle:{card}', SEV_WARN, '◉',
                f'{card} throttling: {why}', ackable=False)

    # ── An allocated logical volume nobody is using ─────────────────────────
    for dev in snap.get('disks', []):
        for part in dev['parts']:
            if part.get('unused_lv'):
                add(f'unusedlv:{part["mount"]}', SEV_INFO, '◫',
                    f'{fmt_bytes(part["total"])} LV allocated, not mounted')

    # Idle GPU while the campaign still has pending work
    camp = snap.get('campaign') or {}
    gpu_busy = max((g['busy'] for g in snap.get('gpus', [])), default=0)
    if camp.get('pending', 0) > 0 and gpu_busy < 5 and not snap.get('sims'):
        add('gpuidle', SEV_INFO, '◉',
            f'GPU idle · {camp["pending"]} sims pending', ackable=False)

    # Repo
    rp = snap.get('repo') or {}
    if rp.get('unpushed'):
        add('unpushed', SEV_WARN, '⎇',
            f'{rp["unpushed"]} unpushed commit(s)', ackable=False)
    if rp.get('dirty'):
        add('dirty', SEV_INFO, '⎇',
            f'{rp["dirty"]} uncommitted file(s)', ackable=False)

    # Journal
    jn = snap.get('journal') or {}
    if jn.get('errors', 0) > 0:
        add(f'journal:{jn["errors"]}', SEV_INFO, '⚠',
            f'{jn["errors"]} journal errors this boot')

    items.sort(key=lambda i: (i['sev'], i['text']))
    return items


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
                import metrics
                self.recorder = metrics.Recorder()
            except Exception as e:
                # Say so. A caller that asked for record=True and silently got
                # no recorder has no way to find out except by noticing an
                # empty database later.
                print(f"collector: metric recording unavailable — {e}",
                      file=sys.stderr, flush=True)
                self.recorder = None

    def _try(self, key, fn, default):
        if self.want is not None and key not in self.want:
            return
        try:
            self.snap[key] = fn()
        except Exception as e:
            self.snap.setdefault(key, default)
            self.snap['_errors'] = self.snap.get('_errors', {})
            self.snap['_errors'][key] = str(e)[:80]

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
        self._try('gpu_metrics', _gpu_metrics, {})
        self._try('ecc', _ecc, {})

        if force_all or now - self._slow_at >= self.SLOW_EVERY:
            self._slow_at = now
            self._try('disks', self.disk.mounts, [])
            # Published by sensord as root; reading them is just a JSON load.
            self._try('bmc', _bmc, {})
            self._try('smart', _smart, {})
            self._try('peripherals', _peripherals, [])
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
            self._try('dimms', _dimms, {})

        self._try('attention', lambda: attention(self.snap), [])
        self.snap['ts'] = time.time()

        if self.recorder is not None:
            # Never let a database problem reach the UI thread.
            try:
                self.recorder.record(self.snap)
            except Exception:
                pass
        return self.snap


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI self-test
# ═══════════════════════════════════════════════════════════════════════════════
def _main():
    if '--dump' not in sys.argv:
        print(__doc__)
        return 0
    want = [a for a in sys.argv[1:] if not a.startswith('-')]
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
