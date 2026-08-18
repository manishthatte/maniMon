#!/usr/bin/env python3
"""
maniMon — privileged sensor sampler.

Runs as root on a systemd timer and writes world-readable JSON into
/run/manimon-sensors/. The panels read those files and never gain privilege.

    sudo python3 sensord.py --once          # sample once, write the files
    sudo python3 sensord.py --once --print  # ...and dump to stdout too
         python3 sensord.py --show          # read back what is there (no root)

WHY A SEPARATE PROCESS
──────────────────────
Four of the most valuable sources on this machine need root:

    /dev/ipmi0        BMC: chassis fans, VRM/DIMM/inlet temps, voltage rails
    nvme smart-log    wear, media errors, thermal-throttle seconds
    smartctl -A       reallocated sectors, TBW, SATA temperature
    dmidecode -t 17   which DIMM slots are populated, and at what speed

The alternative designs were considered and rejected:

  * sudo inside the 2 s panel loop — puts a privilege escalation on the hot
    path, prompts, and blocks a repaint on a 120 ms subprocess.
  * setuid helper — a new setuid binary to audit, for temperature readings.
  * granting the user /dev/ipmi0 via udev — smaller, but hands one desktop
    account raw BMC access, which is a bigger grant than it looks: the BMC can
    power-cycle the host and holds its own user database.

A root process that writes JSON to a tmpfs is the smallest blast radius that
actually works: one file to audit, output that can be inspected by eye, and if
it dies the panels degrade to "stale" instead of breaking.

SAMPLING PERIODS are chosen so nothing expensive lands in the 2 s loop:

    ipmi    30 s    ~120 ms   fans and board temperatures do not move fast
    nvme     5 min  ~15 ms    wear and error counts move over weeks
    sata     5 min  ~80 ms    likewise; -n standby so an idle disk is not woken
    dimms    boot   ~40 ms    only changes when the machine is opened up

Author: Manish Jagdish Thatte
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import SENSOR_DIR as OUT_DIR, CFG                      # noqa: E402

# Absolute paths: this runs from systemd, whose PATH does not include /usr/sbin
# on every distro, and a bare name that silently fails to resolve would look
# exactly like "the sensor is absent".
IPMITOOL = "/usr/bin/ipmitool"
NVME     = "/usr/sbin/nvme"
SMARTCTL = "/usr/sbin/smartctl"
DMIDECODE = "/usr/sbin/dmidecode"

PERIODS = {'ipmi': CFG['sensors']['ipmi_every'],
           'nvme': CFG['sensors']['nvme_every'],
           'sata': CFG['sensors']['sata_every'],
           'dimms': None}                                          # None = once


def _run(cmd, timeout=10):
    """Run a command, return (rc, stdout). Never raises."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout
    except FileNotFoundError:
        return 127, ""
    except subprocess.TimeoutExpired:
        return 124, ""
    except Exception:
        return 1, ""


def _write(name, payload):
    """Atomically publish one sensor file, world-readable."""
    os.makedirs(OUT_DIR, exist_ok=True)
    payload['_ts'] = time.time()
    path = f"{OUT_DIR}/{name}.json"
    tmp = f"{path}.tmp"
    with open(tmp, 'w') as fh:
        json.dump(payload, fh)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


# ═══════════════════════════════════════════════════════════════════════════════
#  IPMI — the BMC sensor data repository
# ═══════════════════════════════════════════════════════════════════════════════
def sample_ipmi():
    """
    `ipmitool sdr elist` output is pipe-delimited, e.g.

        FAN1  | 30h | ok | 29.1 | 1400 RPM
        CPU1  | 01h | ok |  3.1 | 45 degrees C
        P_12V | 60h | ok |  7.1 | 12.10 Volts

    Sensors that are absent or unreadable come back as 'ns'/'disabled' with
    'no reading' in the value column; those are dropped rather than reported
    as zero, because a fan reading 0 RPM and a fan that is not fitted are very
    different facts.
    """
    rc, out = _run([IPMITOOL, 'sdr', 'elist'], timeout=15)
    if rc != 0 or not out.strip():
        return {'ok': False, 'error': f'ipmitool rc={rc}', 'sensors': []}

    sensors = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split('|')]
        if len(parts) < 5:
            continue
        name, _sid, state, _ent, reading = parts[0], parts[1], parts[2], parts[3], parts[4]
        if not name or 'no reading' in reading.lower() or state.lower() in ('ns', 'disabled'):
            continue
        m = re.match(r'^(-?[\d.]+)\s*(.*)$', reading)
        if not m:
            continue
        try:
            value = float(m.group(1))
        except ValueError:
            continue
        unit = m.group(2).strip().lower()
        if unit.startswith('degrees c'):
            kind, unit = 'temp', 'C'
        elif unit.startswith('rpm'):
            kind, unit = 'fan', 'RPM'
        elif unit.startswith('volt'):
            kind, unit = 'volt', 'V'
        elif unit.startswith('watt'):
            kind, unit = 'power', 'W'
        elif unit.startswith('amp'):
            kind, unit = 'current', 'A'
        elif unit.startswith('percent'):
            kind, unit = 'percent', '%'
        else:
            kind = 'other'
        sensors.append({'name': name, 'kind': kind, 'value': value,
                        'unit': unit, 'state': state})
    return {'ok': True, 'sensors': sensors}


# ═══════════════════════════════════════════════════════════════════════════════
#  NVMe SMART
# ═══════════════════════════════════════════════════════════════════════════════
def sample_nvme():
    devs = sorted(d for d in os.listdir('/dev')
                  if re.fullmatch(r'nvme\d+', d)) if os.path.isdir('/dev') else []
    out = {}
    for dev in devs:
        rc, txt = _run([NVME, 'smart-log', f'/dev/{dev}', '-o', 'json'], timeout=10)
        if rc != 0 or not txt.strip():
            continue
        try:
            d = json.loads(txt)
        except Exception:
            continue
        # Kelvin in the raw log; the panel wants Celsius.
        temp = d.get('temperature')
        out[dev] = {
            'temp_c': round(temp - 273.15, 1) if temp and temp > 200 else temp,
            'wear_pct': d.get('percent_used'),
            'spare_pct': d.get('avail_spare'),
            'spare_thresh': d.get('spare_thresh'),
            'power_on_hours': d.get('power_on_hours'),
            'unsafe_shutdowns': d.get('unsafe_shutdowns'),
            'media_errors': d.get('media_errors'),
            'error_log_entries': d.get('num_err_log_entries'),
            'data_units_written': d.get('data_units_written'),
            'data_units_read': d.get('data_units_read'),
            # The two counters that explain a mysteriously slow run.
            'warn_temp_time': d.get('warning_temp_time'),
            'crit_temp_time': d.get('critical_comp_time'),
            'critical_warning': d.get('critical_warning'),
        }
        # data units are 1000 x 512 B per the NVMe spec — not 512, and not KiB.
        for k in ('data_units_written', 'data_units_read'):
            v = out[dev].get(k)
            if isinstance(v, (int, float)):
                out[dev][k.replace('data_units', 'bytes')] = int(v) * 1000 * 512
    return {'ok': bool(out), 'devices': out}


# ═══════════════════════════════════════════════════════════════════════════════
#  SATA SMART
# ═══════════════════════════════════════════════════════════════════════════════
# Attribute IDs worth surfacing. Names vary by vendor; IDs do not.
SATA_ATTRS = {
    5:   'reallocated',
    9:   'power_on_hours',
    12:  'power_cycles',
    177: 'wear_leveling',
    197: 'pending',
    198: 'uncorrectable',
    231: 'ssd_life_left',
}
# NOT in the table above, deliberately:
#
#   194 (temperature) — its raw field is a PACKED BITFIELD on most drives:
#       current | (min << 16) | (max << 32). Read whole it produced
#       210454380576 for sda and 197569675297 for sdc. Masked, sda is
#       32 °C (min 15, max 49) and sdc is 33 °C (min 18, max 46) — and 33 °C
#       is exactly what drivetemp reports for sdc independently. smartctl
#       already normalises this into top-level temperature.current, so that
#       is what we use; the masked attribute is only a fallback.
#
#   241/246 (bytes written) — the UNIT is vendor-defined, so the raw counter
#       is meaningless without knowing the drive. See WRITE_UNITS.
TEMP_ATTR = 194

# Bytes-written is only derived when the unit is genuinely known.
#
# Attribute 246 is conventionally a count of 512 B LBAs, so its name is enough.
# Attribute 241 is VENDOR-DEFINED: different drives count LBAs, 32 MiB chunks
# or whole GiB under the same ID, and some use the name "Total_LBAs_Written"
# while not counting LBAs at all. So for 241 we require a name that states its
# own unit, and otherwise publish the raw counter and refuse to convert.
#
# This is not hypothetical. /dev/sdc reports 241 = 5975. Read as 512 B LBAs
# that is 3 MB written across 3420 power-on hours — impossible; the journal
# alone writes more. Read as GiB it is ~6 TB, i.e. 0.78 drive-writes on a
# 7.68 TB SSD, which sits exactly right next to its own SSD_Life_Left of 99%.
# Both readings cannot be true, so the honest output is the raw number plus the
# attribute name, and a byte figure only once the drive tells us the unit.
WRITE_UNITS_BY_ID = {
    246: {'total_lbas_written': 512},
    241: {
        'host_writes_32mib':   32 * 1024 * 1024,
        'lifetime_writes_gib': 1024 ** 3,
        'host_writes_gib':     1024 ** 3,
        'total_writes_gb':     1000 ** 3,
    },
}
WRITE_ATTR_IDS = (246, 241)

# Per-model overrides for drives whose attribute name lies about its unit.
# Only add an entry with the evidence written down.
#
#   PASCARI S1201K007T68 calls attribute 241 "Total_LBAs_Written" and reports
#   a count that is neither LBAs nor anything else the name suggests.
#
#   MEASURED, not inferred (18 Aug 2026): wrote exactly 8 GiB to the
#   filesystem on this drive, then compared the counter across a SMART
#   refresh. The counter advanced by 8, and 8 x 1 GiB = 8,589,934,592 bytes
#   is exactly the probe size. /sys/block/sdc/stat recorded 8,645,955,584
#   bytes over the same window, the 56 MB excess being ordinary background
#   writes. The alternatives are excluded by margins nothing could explain:
#       512 B LBA -> off by a factor of 2,110,829
#       32 MiB    -> off by a factor of 32
#       1 GB      -> 590 MB short of a measured 8 GiB write
#   So the unit is a gibibyte, and the lifetime figure is real: 6002 GiB,
#   about 6.4 TB, which sits correctly beside the drive's own
#   SSD_Life_Left of 99% on a 7.68 TB SSD.
#
#   Reproduce with: write N GiB, wait for a SMART refresh, watch the delta.
MODEL_WRITE_UNITS = {
    'PASCARI S1201K007T68P029T2100': (241, 1024 ** 3, 'GiB (measured)'),
}


def _smart_read(dev, dtype):
    """One smartctl call. Returns the parsed JSON, or None."""
    # -i as well as -A/-H: model_name lives in the INFO section, and
    # without it MODEL_WRITE_UNITS can never match — the override for a
    # drive that misnames its write counter would silently never fire.
    cmd = [SMARTCTL, '-i', '-A', '-H', '-j', '-n', 'standby']
    if dtype:
        cmd += ['-d', dtype]
    cmd.append(dev)
    # -n standby: never spin up an idle disk just to read its temperature.
    rc, txt = _run(cmd, timeout=15)
    if not txt.strip():
        return None
    try:
        return json.loads(txt)
    except Exception:
        return None


def _smart_fields(d):
    model = (d.get('model_name') or '').strip()
    rec = {
        'passed': (d.get('smart_status') or {}).get('passed'),
        'temp_c': (d.get('temperature') or {}).get('current'),
    }
    if model:
        rec['model'] = model
    table = (d.get('ata_smart_attributes') or {}).get('table', [])
    by_id = {}
    for a in table:
        aid = a.get('id')
        by_id[aid] = a
        key = SATA_ATTRS.get(aid)
        if key:
            rec[key] = (a.get('raw') or {}).get('value')

    # Temperature: trust smartctl's normalised value; fall back to the masked
    # low 16 bits of the packed attribute only if it is missing.
    if rec.get('temp_c') is None and TEMP_ATTR in by_id:
        raw = (by_id[TEMP_ATTR].get('raw') or {}).get('value')
        if isinstance(raw, int):
            cur = raw & 0xFFFF
            if 0 < cur < 200:                     # sanity: a plausible °C
                rec['temp_c'] = cur
                rec['temp_lo'] = (raw >> 16) & 0xFFFF
                rec['temp_hi'] = (raw >> 32) & 0xFFFF

    # Bytes written: only when the attribute NAME tells us the unit.
    for aid in WRITE_ATTR_IDS:
        a = by_id.get(aid)
        if not a:
            continue
        raw = (a.get('raw') or {}).get('value')
        if not isinstance(raw, (int, float)):
            continue
        name = (a.get('name') or '').strip().lower()
        rec['writes_raw'] = int(raw)
        rec['writes_attr'] = f"{aid}:{a.get('name') or '?'}"
        override = MODEL_WRITE_UNITS.get(model)
        if override and override[0] == aid:
            _, mult, label = override
            rec['bytes_written'] = int(raw) * mult
            rec['writes_unit'] = label
            # Only mark approximate when the override says so. A unit
            # established by measurement is not an estimate, and the panel
            # should not hedge it with a tilde.
            rec['writes_inferred'] = 'inferred' in label.lower()
            break
        mult = WRITE_UNITS_BY_ID.get(aid, {}).get(name)
        if mult:
            rec['bytes_written'] = int(raw) * mult
        else:
            rec['writes_unit'] = 'unknown'
        break
    return rec


def sample_sata():
    rc, scan = _run([SMARTCTL, '--scan'], timeout=10)
    devs = []
    for line in scan.splitlines():
        # Honour the type smartctl itself reports. It matters: the two USB
        # backup disks scan as "-d sat", but /dev/sdc — the 7.68 TB SSD that
        # carries /home, the single most important drive here — scans as
        # "-d scsi" because it sits behind a SAS controller. Read as SCSI it
        # has no ata_smart_attributes table at all, so every wear, reallocated
        # and lifetime-written figure comes back empty for the one disk whose
        # wear actually matters.
        m = re.match(r'^(/dev/\S+)(?:\s+-d\s+(\S+))?', line)
        if m and 'nvme' not in m.group(1):
            devs.append((m.group(1), m.group(2)))
    out = {}
    for dev, dtype in devs:
        d = _smart_read(dev, dtype)
        rec = _smart_fields(d) if d else None
        # A SCSI-presented SATA disk answers the health question but not the
        # attribute question. When nothing useful came back, ask again through
        # the SAT translation layer, which is how the ATA attributes surface.
        if dtype != 'sat' and (rec is None or len(rec) <= 2):
            d2 = _smart_read(dev, 'sat')
            if d2:
                alt = _smart_fields(d2)
                if len(alt) > len(rec or {}):
                    rec, dtype = alt, 'sat'
        if not rec:
            continue
        rec['smart_type'] = dtype or 'auto'
        out[os.path.basename(dev)] = rec
    return {'ok': bool(out), 'devices': out}


# ═══════════════════════════════════════════════════════════════════════════════
#  DIMM inventory — memory channel population
# ═══════════════════════════════════════════════════════════════════════════════
def sample_dimms():
    """
    Which slots are filled, and therefore how many memory channels are live.

    This exists because the single best upgrade on this machine is filling the
    four empty slots: 4 of 12 channels populated is ~153 of a possible
    461 GB/s, and every DFT/GW/NEGF run here is bandwidth-bound. The panel
    should show that gap, and should show it closing when the RDIMMs arrive.
    """
    rc, out = _run([DMIDECODE, '-t', '17'], timeout=15)
    if rc != 0 or not out.strip():
        return {'ok': False, 'error': f'dmidecode rc={rc}', 'slots': []}
    slots, cur = [], None
    for line in out.splitlines():
        s = line.strip()
        if s.startswith('Memory Device'):
            if cur:
                slots.append(cur)
            cur = {}
            continue
        if cur is None or ':' not in s:
            continue
        k, v = [x.strip() for x in s.split(':', 1)]
        if k == 'Locator':
            cur['locator'] = v
        elif k == 'Bank Locator':
            cur['bank'] = v
        elif k == 'Size':
            cur['size'] = v
        elif k == 'Speed':
            cur['speed'] = v
        elif k == 'Configured Memory Speed':
            cur['configured_speed'] = v
        elif k == 'Rank':
            cur['rank'] = v
        elif k == 'Type':
            cur['type'] = v
        elif k == 'Manufacturer':
            cur['vendor'] = v
        elif k == 'Part Number':
            cur['part'] = v
    if cur:
        slots.append(cur)

    populated = [s for s in slots
                 if s.get('size') and 'No Module' not in s.get('size', '')]
    mts = 0
    for s in populated:
        m = re.search(r'(\d+)', s.get('configured_speed') or s.get('speed') or '')
        if m:
            mts = max(mts, int(m.group(1)))
    return {
        'ok': True,
        'slots': slots,
        'total_slots': len(slots),
        'populated': len(populated),
        # 8 bytes per channel per transfer. This is the theoretical ceiling of
        # the populated channels, not a measurement.
        'mts': mts,
        'gbs_theoretical': round(len(populated) * mts * 8 / 1000.0, 1) if mts else None,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  Driver
# ═══════════════════════════════════════════════════════════════════════════════
SAMPLERS = {
    'ipmi':  sample_ipmi,
    'nvme':  sample_nvme,
    'sata':  sample_sata,
    'dimms': sample_dimms,
}


def _is_due(name, force=False):
    """
    True if this sampler's published file is older than its period.

    The systemd timer fires every 30 s and calls --once, so without this every
    tick would shell out to nvme, smartctl AND dmidecode as well — three
    subprocesses per 30 s to re-read counters that move over weeks. Age is
    taken from the published file's mtime, which means the rate limit survives
    the process exiting between ticks, as a oneshot service always does.
    """
    if force:
        return True
    period = PERIODS.get(name)
    path = f"{OUT_DIR}/{name}.json"
    if not os.path.exists(path):
        return True                     # never sampled
    if period is None:
        return False                    # boot-only (dimms) and already present
    return (time.time() - os.path.getmtime(path)) >= period


def sample_once(which=None, verbose=False, force=False):
    names = which or list(SAMPLERS)
    results = {}
    for n in names:
        if not _is_due(n, force):
            if verbose:
                print(f"[{n}] skipped — still fresh")
            continue
        t0 = time.monotonic()
        try:
            payload = SAMPLERS[n]()
        except Exception as e:                      # a sampler must never kill the run
            payload = {'ok': False, 'error': str(e)[:200]}
        payload['_ms'] = round((time.monotonic() - t0) * 1000, 1)
        _write(n, payload)
        results[n] = payload
        if verbose:
            print(f"[{n}] {payload['_ms']:.0f} ms  ok={payload.get('ok')}")
    return results


def show():
    """Read back the published files — no root needed. For eyeballing."""
    if not os.path.isdir(OUT_DIR):
        print(f"{OUT_DIR} does not exist — sensord has not run.")
        return 1
    for f in sorted(os.listdir(OUT_DIR)):
        if not f.endswith('.json'):
            continue
        with open(f"{OUT_DIR}/{f}") as fh:
            d = json.load(fh)
        age = time.time() - d.get('_ts', 0)
        print(f"── {f}  ({age:.0f}s old, {d.get('_ms', '?')} ms) ──")
        print(json.dumps(d, indent=2)[:4000])
        print()
    return 0


def preflight():
    """Report which tools are actually present, before anything is sampled."""
    print("tool availability:")
    ok = True
    for label, path in (('ipmitool', IPMITOOL), ('nvme', NVME),
                        ('smartctl', SMARTCTL), ('dmidecode', DMIDECODE)):
        present = os.path.exists(path) or shutil.which(label)
        print(f"  {label:10} {'present' if present else '** MISSING **'}  {path}")
        ok = ok and bool(present)
    print(f"  /dev/ipmi0 {'present' if os.path.exists('/dev/ipmi0') else '** ABSENT **'}")
    print(f"  euid       {os.geteuid()}{'  (root)' if os.geteuid() == 0 else '  — NOT root'}")
    return 0 if ok else 1


def main():
    args = sys.argv[1:]
    if '--show' in args:
        return show()
    if '--preflight' in args:
        return preflight()

    if os.geteuid() != 0:
        print("sensord must run as root (it reads /dev/ipmi0 and raw disks).",
              file=sys.stderr)
        print("  sudo python3 sensord.py --once --print", file=sys.stderr)
        return 2

    which = [a for a in args if a in SAMPLERS] or None
    verbose = '--print' in args or '--once' in args

    if '--once' in args:
        # --force ignores the per-source rate limit. Use it when hand-testing;
        # the timer must not, or it defeats the point of the limit.
        res = sample_once(which, verbose=verbose, force='--force' in args)
        if '--print' in args:
            print(json.dumps(res, indent=2, default=str))
        return 0

    # Daemon mode: one process, per-source periods. The systemd timer calls
    # --once instead, so this is here for hand-testing and for anyone who would
    # rather run it as a long-lived service.
    sample_once(['dimms'])
    while True:
        sample_once()          # _is_due() applies the per-source period
        time.sleep(2)


if __name__ == '__main__':
    sys.exit(main())
