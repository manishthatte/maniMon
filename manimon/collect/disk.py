"""
Filesystems, block devices, drive temperatures and per-device I/O rates.
"""

import os, re, glob, time

from ..util import rf, ri, read_temps, hwmon_by_name
from ..sensors import published


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
    for hw in hwmon_by_name('nvme'):
        t = read_temps(hw)
        val = t.get('Composite') or (list(t.values())[0] if t else None)
        real = os.path.realpath(f'{hw}/device')
        blk = glob.glob(f'{real}/nvme/nvme*/nvme*n[0-9]') or \
              glob.glob(f'{os.path.dirname(real)}/nvme/nvme*/nvme*n[0-9]')
        if val:
            if blk:
                out[os.path.basename(blk[0])] = val
            else:
                out.setdefault('nvme0n1', val)
    for hw in hwmon_by_name('drivetemp'):
        t = read_temps(hw)
        val = list(t.values())[0] if t else None
        blk = glob.glob(f'{os.path.realpath(f"{hw}/device")}/block/*')
        if val and blk:
            out[os.path.basename(blk[0])] = val
    return out


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
        for vg in published.lvm_volumes():
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
