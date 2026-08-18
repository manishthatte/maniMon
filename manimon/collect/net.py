"""
Network interfaces, link state, throughput, WAN reachability and sockets.
"""

import os, re, glob, time

from ..util import rf, ri, sh, read_temps


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
            t = read_temps(hw)
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
