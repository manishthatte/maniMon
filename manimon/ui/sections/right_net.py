"""
Network interfaces, throughput and WAN reachability.

Both halves of one section live here: the widgets it creates and the code that
fills them.
"""

import time

from .. import widgets as W
from ..window import *   # noqa: F401,F403
from ..markup import kv
from ...util import fmt_bytes, fmt_rate

MAX_NICS = 2


def build(p):
        # 9. Network  (relocated from the left panel — no room there at 1440px)
        p.head("◎", "NETWORK", TEAL)
        for i in range(MAX_NICS):
            n = p.vbox()
            p._wid[f"nicbox{i}"] = n
            p.lbl(f"nic{i}_hdr", c=n)
            p.lbl(f"nic{i}_ip", c=n)
            p.wid(f"nic{i}_spark", W.DualSpark(height=22), c=n)
            p.lbl(f"nic{i}_rate", c=n)
        p.lbl("net_wan", mt=2)


def refresh(p, s):
    nics = [n for n in (s.get('net') or [])
            if (n['up'] and n['ipv4']) or n['rx_bps'] > 0]
    if not nics:
        nics = [n for n in (s.get('net') or []) if n['up']][:1]
    for i in range(MAX_NICS):
        box = p._wid.get(f"nicbox{i}")
        if i >= len(nics):
            if box:
                box.set_visible(False)
                box.set_no_show_all(True)
            continue
        if box:
            box.set_visible(True)
            box.set_no_show_all(False)
        n = nics[i]
        scol = W.OK if n['up'] else DIM
        spd = f'{n["speed_mbps"]} Mb/s' if n['speed_mbps'] else '—'
        t = n.get('temp')
        p.L(f"nic{i}_hdr",
               f'<span font="{FB}" foreground="{TEAL}">{n["iface"][:16]}</span>'
               f'<span font="{FXS}" foreground="{scol}">  {n["state"]}</span>'
               f'<span font="{FXS}" foreground="{DIM}">  {spd}</span>'
               + (f'<span font="{FXS}" foreground="{W.temp_color(t,75,90)}">'
                  f'  {t:.0f}°C</span>' if t else ''))
        p.L(f"nic{i}_ip",
               f'<span font="{FXS}" foreground="{DIM}">  {n["ipv4"] or "no address"}'
               f'   {(n["ipv6"] or "")[:24]}</span>')
        sp = p._wid.get(f"nic{i}_spark")
        if sp:
            sp.push(n['rx_bps'], n['tx_bps'])
        ecol = W.WARN if (n['errors'] + n['dropped']) else DIM
        p.L(f"nic{i}_rate",
               f'<span font="{FXS}" foreground="{W.CAT[0]}">  ↓ {fmt_rate(n["rx_bps"])}</span>'
               f'<span font="{FXS}" foreground="{W.CAT[1]}">   ↑ {fmt_rate(n["tx_bps"])}</span>'
               f'<span font="{FXS}" foreground="{DIM}">   Σ {fmt_bytes(n["rx_total"])}'
               f'/{fmt_bytes(n["tx_total"])}</span>'
               f'<span font="{FXS}" foreground="{ecol}">   e{n["errors"]}'
               f'/d{n["dropped"]}</span>')

    wan = s.get('wan') or {}
    sk = s.get('sockets') or {}
    if wan.get('up'):
        ms = wan.get('ms') or 0
        p.L("net_wan",
               kv("WAN", f'{ms:.0f} ms', W.OK if ms < 60 else W.WARN) +
               kv("   tcp", f'{sk.get("tcp",0)}') +
               kv("  listen", f'{sk.get("listening",0)}'))
    else:
        p.L("net_wan",
               f'<span font="{FS}" foreground="{W.CRIT}">WAN  unreachable</span>')
