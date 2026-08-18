#!/usr/bin/env python3
"""
maniMon — health and deep-sensor readers.

The unprivileged half of the sensor work. Two kinds of source live here:

  1. Things `sensord.py` published as root into /run/manimon-sensors/ —
     BMC/IPMI, NVMe and SATA SMART, the DIMM inventory. Read as JSON.
  2. Things that were readable all along and the monitor simply never read —
     the amdgpu `gpu_metrics` blob, EDAC ECC counters, HID peripheral
     batteries, LVM volume-group capacity.

    python3 health.py            print everything this module can see

Staleness is explicit. Every published reading carries the age of the file it
came from, and anything older than STALE_AFTER is flagged rather than shown as
current — a fan speed from twenty minutes ago is not a fan speed.

Author: Manish Jagdish Thatte
"""

import glob
import json
import os
import re
import struct
import sys
import time

from ..config import SENSOR_DIR
STALE_AFTER = 180.0          # seconds; the timer runs every 30 s


def _read_published(name):
    """Load one sensord file. Returns (data, age_seconds) or (None, None)."""
    path = f"{SENSOR_DIR}/{name}.json"
    try:
        with open(path) as fh:
            d = json.load(fh)
        return d, time.time() - d.get('_ts', os.path.getmtime(path))
    except Exception:
        return None, None


def _rf(path, default=""):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except Exception:
        return default


def _ri(path, default=0):
    try:
        return int(_rf(path, str(default)))
    except Exception:
        return default


# ═══════════════════════════════════════════════════════════════════════════════
#  BMC — chassis fans, board temperatures, voltage rails, PSU
# ═══════════════════════════════════════════════════════════════════════════════
def bmc():
    """
    The board-level sensor tier, which the monitor previously read nothing of.

    Returns a summary plus the raw sensor list, so the panel can show
    "6 fans 1400-2100 rpm, board 47 C max" without hardcoding this board's
    sensor names — they differ between BMC firmware revisions and there is no
    reason for the panel to know them.
    """
    d, age = _read_published('ipmi')
    if d is None:
        return {'present': False, 'reason': 'sensord has not published ipmi.json'}
    if not d.get('ok'):
        return {'present': False, 'reason': d.get('error', 'ipmitool failed')}

    sensors = d.get('sensors', [])
    by_kind = {}
    for s in sensors:
        by_kind.setdefault(s['kind'], []).append(s)

    fans = [s['value'] for s in by_kind.get('fan', [])]
    temps = [s['value'] for s in by_kind.get('temp', [])]
    watts = [s['value'] for s in by_kind.get('power', [])]
    volts = by_kind.get('volt', [])

    # A fan reporting 0 while the others spin is a failed fan, not an idle one.
    dead_fans = [s['name'] for s in by_kind.get('fan', [])
                 if s['value'] == 0 and max(fans, default=0) > 0]

    return {
        'present': True,
        'stale': age is not None and age > STALE_AFTER,
        'age': age,
        'n_sensors': len(sensors),
        'fans': by_kind.get('fan', []),
        'temps': by_kind.get('temp', []),
        'volts': volts,
        'fan_count': len(fans),
        'fan_min': min(fans) if fans else None,
        'fan_max': max(fans) if fans else None,
        'dead_fans': dead_fans,
        'temp_max': max(temps) if temps else None,
        'temp_hottest': (max(by_kind.get('temp', []), key=lambda s: s['value'])['name']
                         if temps else None),
        'power': max(watts) if watts else None,
        # A rail more than 5% off nominal is worth flagging.
        'rails_off_nominal': [
            s['name'] for s in volts
            if _rail_nominal(s['name']) and
            abs(s['value'] - _rail_nominal(s['name'])) / _rail_nominal(s['name']) > 0.05
        ],
    }


def _rail_nominal(name):
    """Nominal voltage guessed from the rail's name, e.g. 'P_12V' -> 12.0."""
    m = re.search(r'(\d+)V(\d+)?', name.upper())
    if not m:
        return None
    v = float(m.group(1))
    if m.group(2):
        v += float(m.group(2)) / 10.0
    return v if 0.5 <= v <= 15 else None


# ═══════════════════════════════════════════════════════════════════════════════
#  Storage health — SMART
# ═══════════════════════════════════════════════════════════════════════════════
def smart():
    """
    Per-device wear and error state, merged from the NVMe and SATA samplers.

    `life_pct` is remaining life where the drive reports it. NVMe reports
    percent USED, SATA usually reports percent REMAINING, and conflating the
    two would put a healthy drive at 2% life. They are converted here, once.
    """
    out = {}
    nv, nv_age = _read_published('nvme')
    if nv and nv.get('ok'):
        for dev, d in (nv.get('devices') or {}).items():
            blk = f"{dev}n1"                      # nvme0 -> nvme0n1
            wear = d.get('wear_pct')
            out[blk] = {
                'kind': 'nvme',
                'temp': d.get('temp_c'),
                'life_pct': (100 - wear) if isinstance(wear, (int, float)) else None,
                'spare_pct': d.get('spare_pct'),
                'power_on_hours': d.get('power_on_hours'),
                'bytes_written': d.get('bytes_written'),
                'media_errors': d.get('media_errors'),
                'unsafe_shutdowns': d.get('unsafe_shutdowns'),
                # Seconds spent over the warning/critical thermal thresholds.
                # Non-zero here is the answer to "why was that run slow".
                'warn_temp_time': d.get('warn_temp_time'),
                'crit_temp_time': d.get('crit_temp_time'),
                'critical_warning': d.get('critical_warning'),
                'healthy': not d.get('critical_warning'),
                'age': nv_age,
            }
    sa, sa_age = _read_published('sata')
    if sa and sa.get('ok'):
        for dev, d in (sa.get('devices') or {}).items():
            life = d.get('ssd_life_left')
            if life is None and isinstance(d.get('wear_leveling'), (int, float)):
                life = d['wear_leveling']
            out[dev] = {
                'kind': 'sata',
                'temp': d.get('temp_c'),
                'life_pct': life if isinstance(life, (int, float)) and life <= 100 else None,
                'power_on_hours': d.get('power_on_hours'),
                'bytes_written': d.get('bytes_written'),
                # Present only when the drive's write counter has no unit we
                # trust; the panel shows the bare number rather than pretending
                # the drive has never been written to. See sensord.WRITE_UNITS_BY_ID.
                'writes_raw': d.get('writes_raw'),
                'writes_attr': d.get('writes_attr'),
                'writes_inferred': d.get('writes_inferred'),
                'reallocated': d.get('reallocated'),
                'pending': d.get('pending'),
                'uncorrectable': d.get('uncorrectable'),
                'healthy': d.get('passed'),
                'age': sa_age,
            }
    return out


# ═══════════════════════════════════════════════════════════════════════════════
#  Memory channels
# ═══════════════════════════════════════════════════════════════════════════════
def dimms():
    """
    Slot population and the resulting memory-bandwidth ceiling.

    This machine runs 4 of its 8 slots on a 12-channel controller, so it reaches
    roughly a third of the bandwidth the CPU can address — and every DFT, GW and
    NEGF run here is bandwidth-bound. Four matching RDIMMs in the empty slots is
    the cheapest real speedup available, so the panel shows the gap, and will
    show it close when they land.
    """
    d, age = _read_published('dimms')
    if d is None or not d.get('ok'):
        return {'present': False}
    populated, total = d.get('populated', 0), d.get('total_slots', 0)
    return {
        'present': True,
        'age': age,
        'slots': d.get('slots', []),
        'populated': populated,
        'total_slots': total,
        'empty': total - populated,
        'mts': d.get('mts'),
        'gbs': d.get('gbs_theoretical'),
        # The EPYC 9334 is a 12-channel part. The board only has 8 slots, so 8
        # is the reachable ceiling here without a different board.
        'gbs_board_max': (round(total * d['mts'] * 8 / 1000.0, 1)
                          if d.get('mts') and total else None),
        'gbs_cpu_max': (round(12 * d['mts'] * 8 / 1000.0, 1)
                        if d.get('mts') else None),
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  ECC
# ═══════════════════════════════════════════════════════════════════════════════
def ecc():
    """
    Correctable and uncorrectable ECC counts from EDAC.

    Readable without privilege, and the monitor never looked. A rising
    correctable count is the earliest warning a DIMM is going — which matters
    more, not less, once the empty slots are filled.
    """
    mcs = sorted(glob.glob('/sys/devices/system/edac/mc/mc[0-9]*'))
    if not mcs:
        return {'present': False}
    ce = sum(_ri(f'{m}/ce_count') for m in mcs)
    ue = sum(_ri(f'{m}/ue_count') for m in mcs)
    per_dimm = []
    for m in mcs:
        for d in sorted(glob.glob(f'{m}/dimm*')):
            dce, due = _ri(f'{d}/dimm_ce_count'), _ri(f'{d}/dimm_ue_count')
            if dce or due:
                per_dimm.append({'dimm': _rf(f'{d}/dimm_label') or os.path.basename(d),
                                 'ce': dce, 'ue': due})
    return {'present': True, 'ce': ce, 'ue': ue,
            'controllers': len(mcs), 'per_dimm': per_dimm}


# ═══════════════════════════════════════════════════════════════════════════════
#  amdgpu gpu_metrics
# ═══════════════════════════════════════════════════════════════════════════════
# Verified against this card on 18 Aug 2026: format_revision=1,
# content_revision=3, structure_size=120 — i.e. struct gpu_metrics_v1_3. The
# layout was checked against known-good values rather than trusted from a
# header file: current_uclk read 456 MHz at the same moment hwmon reported
# mclk 456 MHz, and temperature_hotspot matched the junction reading.
_V1_3 = struct.Struct(
    '<'      # little-endian, and the struct is naturally aligned as written
    'HBB'    # structure_size, format_revision, content_revision
    'HHHHHH' # temperature: edge, hotspot, mem, vrgfx, vrsoc, vrmem
    'HHH'    # activity: gfx, umc (memory controller), mm
    'H'      # average_socket_power  -> ends at offset 24
    # NO padding here. The obvious assumption is that the 64-bit
    # energy_accumulator must be 8-byte aligned and therefore needs two pad
    # bytes; adding them gives a 122-byte struct that silently fails to match
    # the 120-byte blob. The live bytes settle it: average_socket_power reads
    # at offset 22 and energy_accumulator at 24, adjacent.
    'Q'      # energy_accumulator
    'Q'      # system_clock_counter
    'HHHHHHH'  # average clocks: gfx, soc, u, vclk0, dclk0, vclk1, dclk1
    'HHHHHHH'  # current clocks: gfx, soc, u, vclk0, dclk0, vclk1, dclk1
    'I'      # throttle_status
    'H'      # current_fan_speed
    'HH'     # pcie_link_width, pcie_link_speed
    'H'      # padding
    'II'     # gfx_activity_acc, mem_activity_acc
    '4H'     # temperature_hbm[4]
    'Q'      # firmware_timestamp
    'HHH'    # voltage_soc, voltage_gfx, voltage_mem
    'H'      # padding1
    'Q'      # indep_throttle_status
)

# Bit meanings for indep_throttle_status, from the kernel's `enum smu_throttler`
# (amdgpu_smu.h). Transcribed from the enum, not guessed: an earlier draft here
# had bit 36 as TEMP_VR_SOC, which decoded this card's idle reading into a
# "VR SOC throttling" alarm at 47 C. Bit 36 is TEMP_HOTSPOT.
_THROTTLE_BITS = {
    # Power
    0: 'PPT0', 1: 'PPT1', 2: 'PPT2', 3: 'PPT3',
    4: 'SPL', 5: 'FPPT', 6: 'SPPT', 7: 'SPPT_APU',
    # Current
    16: 'TDC_GFX', 17: 'TDC_SOC', 18: 'TDC_MEM', 19: 'TDC_VDD',
    20: 'TDC_CVIP', 21: 'EDC_CPU', 22: 'EDC_GFX', 23: 'APCC',
    # Temperature
    32: 'TEMP_GPU', 33: 'TEMP_CORE', 34: 'TEMP_MEM', 35: 'TEMP_EDGE',
    36: 'TEMP_HOTSPOT', 37: 'TEMP_SOC', 38: 'TEMP_VR_GFX', 39: 'TEMP_VR_SOC',
    40: 'TEMP_VR_MEM0', 41: 'TEMP_VR_MEM1', 42: 'TEMP_LIQUID0',
    43: 'TEMP_LIQUID1', 44: 'VRHOT0', 45: 'VRHOT1',
    46: 'PROCHOT_CPU', 47: 'PROCHOT_GFX',
    # Other
    56: 'PPM', 57: 'FIT',
}

# UNVERIFIED ON THIS CARD — read this before trusting `throttled`.
#
# At idle, 1% GFX activity and 47 C hotspot against a 110 C limit, this W7900
# reports indep_throttle_status = 1<<36 (TEMP_HOTSPOT). It is not physically
# possible to be hotspot-throttling 63 degrees below the limit, so on this
# card/firmware the field is NOT a live "currently throttling" indicator — most
# likely it reports which limiter is armed, or is simply not populated.
#
# So the flag is deliberately NOT allowed to raise an alarm while the GPU is
# idle. Driving the attention queue off it would produce a permanent false
# "GPU throttled" warning, and a monitor that cries wolf gets ignored.
# Confirm under real load (a GPAW or LAMMPS run pushing junction past 85 C)
# and tighten this if the bits then track reality.
THROTTLE_LOAD_FLOOR = 20         # % GFX activity below which the flag is muted


def gpu_metrics(card=None):
    """
    Parse the amdgpu gpu_metrics blob for one card.

    Everything here is world-readable and none of it was being used. It adds
    three VR temperatures that hwmon does NOT expose (vrgfx, vrsoc, vrmem),
    the throttle status — the direct answer to "why is the GPU slow", which
    nothing on the panel could answer before — plus socket power averaged by
    firmware rather than sampled, and the three rail voltages.
    """
    cards = [card] if card else [os.path.basename(p) for p in
                                 sorted(glob.glob('/sys/class/drm/card[0-9]*'))
                                 if os.path.exists(f'{p}/device/gpu_metrics')]
    out = {}
    for c in cards:
        path = f'/sys/class/drm/{c}/device/gpu_metrics'
        try:
            with open(path, 'rb') as fh:
                raw = fh.read()
        except Exception:
            continue
        parsed = parse_gpu_metrics(raw)
        if parsed is not None:
            out[c] = parsed
    return out


def parse_gpu_metrics(raw):
    """Decode one gpu_metrics blob. Returns a dict, or None if unreadable.

    Split out from the sysfs read so the offsets can be tested against known
    bytes. They were verified against live bytes on an RDNA 3 card rather than
    taken from a header — the first assumed layout was wrong by two bytes.
    """
    if len(raw) < 4:
        return None
    size, fmt_rev, cont_rev = struct.unpack_from('<HBB', raw, 0)
    if (fmt_rev, cont_rev) != (1, 3) or len(raw) < _V1_3.size:
        # Do not guess at an unknown layout — a misparse would produce
        # plausible-looking numbers, which is worse than none.
        return {'supported': False,
                'version': f'v{fmt_rev}_{cont_rev}', 'size': size}
    f = _V1_3.unpack(raw[:_V1_3.size])
    i = iter(f)
    next(i); next(i); next(i)                       # header
    t_edge, t_hot, t_mem, t_vrgfx, t_vrsoc, t_vrmem = (next(i) for _ in range(6))
    act_gfx, act_umc, act_mm = (next(i) for _ in range(3))
    socket_power = next(i)
    energy_acc = next(i)
    next(i)                                          # system_clock_counter
    avg = [next(i) for _ in range(7)]
    cur = [next(i) for _ in range(7)]
    throttle_status = next(i)
    fan = next(i)
    link_width, link_speed = next(i), next(i)
    next(i)                                          # padding
    gfx_acc, mem_acc = next(i), next(i)
    [next(i) for _ in range(4)]                      # temperature_hbm
    next(i)                                          # firmware_timestamp
    v_soc, v_gfx, v_mem = next(i), next(i), next(i)
    next(i)                                          # padding1
    indep_throttle = next(i)

    def valid(v, width=16):
        """
        Firmware writes an all-ones field to mean 'not populated'.

        Widths are checked up to the declared one rather than only AT it:
        this card writes 0xFFFFFFFF into the 64-bit energy_accumulator, so
        testing 64-bit all-ones alone would let 4294967295 through and the
        panel would show it as a real energy total.
        """
        for w in (16, 32, 64):
            if w <= width and v == (1 << w) - 1:
                return None
        return v

    reasons = sorted(name for bit, name in _THROTTLE_BITS.items()
                     if indep_throttle & (1 << bit))
    # See THROTTLE_LOAD_FLOOR: the bits are not trustworthy at idle on this
    # card, so an idle GPU never reports as throttled.
    busy_enough = (act_gfx or 0) >= THROTTLE_LOAD_FLOOR
    throttled = bool(indep_throttle) and busy_enough
    return {
        'supported': True,
        'version': 'v1_3',
        'temp_edge': valid(t_edge), 'temp_hotspot': valid(t_hot),
        'temp_mem': valid(t_mem),
        # Not available from hwmon at all — the card's own VR temperatures.
        'temp_vr_gfx': valid(t_vrgfx), 'temp_vr_soc': valid(t_vrsoc),
        'temp_vr_mem': valid(t_vrmem),
        'act_gfx': valid(act_gfx), 'act_umc': valid(act_umc),
        'act_mm': valid(act_mm),
        'socket_power': valid(socket_power),
        # These three read all-ones on this card — firmware does not fill
        # them. Reporting 4294967295 as an energy total would be nonsense.
        'energy_acc': valid(energy_acc, 64),
        'avg_gfxclk': valid(avg[0]), 'avg_uclk': valid(avg[2]),
        'cur_gfxclk': valid(cur[0]), 'cur_socclk': valid(cur[1]),
        'cur_uclk': valid(cur[2]),
        'fan_rpm': valid(fan),
        'link_width': valid(link_width),
        'link_speed_gts': valid(link_speed) / 10.0 if valid(link_speed) else None,
        'gfx_activity_acc': valid(gfx_acc, 32),
        'mem_activity_acc': valid(mem_acc, 32),
        'voltage_soc': valid(v_soc), 'voltage_gfx': valid(v_gfx),
        'voltage_mem': valid(v_mem),
        'throttled': throttled,
        'throttle_reasons': reasons if throttled else [],
        # Kept regardless so the raw state can be inspected, and so the
        # idle-mute above can be re-checked under load without a code change.
        'throttle_bits_raw': reasons,
        'throttle_raw': indep_throttle,
        'throttle_trusted': busy_enough,
        'throttle_status_asic': throttle_status,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  LVM
# ═══════════════════════════════════════════════════════════════════════════════
def lvm():
    """
    Volume groups and their logical volumes, read from sysfs rather than by
    shelling out to `lvs` (which needs root and warns loudly without it).

    The reason this exists: after the 17 Aug reinstall a 1.5 TB logical volume
    sits allocated and unmounted, consuming most of the NVMe. An unmounted LV
    is invisible to anything that walks /proc/mounts, so the old storage
    section could not show it at all — the disk simply looked emptier than it is.
    """
    vgs = {}
    for dm in sorted(glob.glob('/sys/block/dm-*')):
        name = _rf(f'{dm}/dm/name')
        if not name or '-' not in name:
            continue
        # LVM escapes a literal '-' in a name by doubling it.
        vg = re.split(r'(?<!-)-(?!-)', name, maxsplit=1)[0].replace('--', '-')
        lv = re.split(r'(?<!-)-(?!-)', name, maxsplit=1)[1].replace('--', '-')
        size = _ri(f'{dm}/size') * 512
        dev = os.path.basename(dm)               # e.g. dm-2
        mount = None
        for line in _rf('/proc/mounts').split('\n'):
            p = line.split()
            if len(p) >= 2 and (p[0].endswith(f'/{name}') or
                                os.path.realpath(p[0]) == f'/dev/{dev}'):
                mount = p[1]
                break
        if mount is None:
            # /proc/swaps names the device by its RESOLVED path — this machine
            # shows "/dev/dm-2", never "/dev/mapper/debian--vg-swap_1". Matching
            # on the mapper name found nothing and reported active swap as an
            # unused volume, which is the opposite of the truth.
            for line in _rf('/proc/swaps').split('\n')[1:]:
                first = line.split()[0] if line.split() else ''
                if not first.startswith('/dev/'):
                    continue
                if os.path.realpath(first) == f'/dev/{dev}' or first.endswith(f'/{name}'):
                    mount = '[swap]'
                    break
        vgs.setdefault(vg, {'vg': vg, 'lvs': [], 'allocated': 0})
        vgs[vg]['lvs'].append({'lv': lv, 'dm': dev, 'size': size,
                               'mount': mount, 'unused': mount is None})
        vgs[vg]['allocated'] += size
    return list(vgs.values())


# ═══════════════════════════════════════════════════════════════════════════════
#  Peripherals
# ═══════════════════════════════════════════════════════════════════════════════
def peripherals():
    """Battery level of HID devices (the Logitech keyboard and mouse)."""
    # These devices expose `capacity_level` (Full/High/Normal/Low/Critical) and
    # NOT a numeric `capacity` — the K540 keyboard reports a coarse level only.
    # Requiring `capacity` found nothing at all and silently returned an empty
    # list, which looked identical to "no peripherals connected".
    LEVELS = {'Full': 100, 'High': 75, 'Normal': 55, 'Low': 20, 'Critical': 5}
    out = []
    for p in sorted(glob.glob('/sys/class/power_supply/hidpp_battery_*')):
        cap = _rf(f'{p}/capacity')
        level = _rf(f'{p}/capacity_level')
        if not cap and not level:
            continue
        out.append({
            'name': _rf(f'{p}/model_name') or os.path.basename(p),
            'capacity': int(cap) if cap.isdigit() else LEVELS.get(level),
            'exact': bool(cap.isdigit()) if cap else False,
            'level': level or None,
            'status': _rf(f'{p}/status'),
            'online': _rf(f'{p}/online') == '1',
        })
    return out


# ═══════════════════════════════════════════════════════════════════════════════
#  Facade
# ═══════════════════════════════════════════════════════════════════════════════
def read_all():
    return {
        'bmc': bmc(),
        'smart': smart(),
        'dimms': dimms(),
        'ecc': ecc(),
        'gpu_metrics': gpu_metrics(),
        'lvm': lvm(),
        'peripherals': peripherals(),
    }


def _main():
    d = read_all()
    print(json.dumps(d, indent=2, default=str))
    if not d['bmc'].get('present'):
        print(f"\nNOTE: {d['bmc'].get('reason')}", file=sys.stderr)
        print("      sudo systemctl start manimon-sensors.service", file=sys.stderr)
    return 0


if __name__ == '__main__':
    import sys
    raise SystemExit(_main())
