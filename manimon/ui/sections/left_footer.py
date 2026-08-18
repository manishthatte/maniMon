"""
Footer: uptime, host facts and the refresh stamp.

Both halves of one section live here: the widgets it creates and the code that
fills them. They used to sit 150 lines apart in a single 850-line module.
"""

import time

from .. import widgets as W
from ..window import *   # noqa: F401,F403
from ...util import fmt_bytes, fmt_rate, fmt_elapsed

def build(p):
        # 8. Footer ------------------------------------------------------------
        p.box.pack_start(Gtk.Separator(), False, False, 0)
        p.lbl("foot1", mt=3)
        p.lbl("foot2")


def refresh(p, s):
    si = s.get('sysinfo') or {}
    # ROCm missing while /dev/kfd exists is a real, work-blocking state —
    # the GPU is a display adapter. Say so instead of printing "?".
    rocm = si.get('rocm', '?')
    rocm_col = DIM if si.get('rocm_ok') else W.WARN
    p.L("foot1",
           f'<span font="{FXS}" foreground="{DIM}">{si.get("host","?")}  ·  '
           f'{si.get("kernel","?")}  ·  ROCm </span>'
           f'<span font="{FXS}" foreground="{rocm_col}">{rocm}</span>')

    # Store state, so it is obvious whether statistics are accumulating.
    store = ''
    if getattr(p, 'stats', None) and p.stats.available:
        n = p._stat('cpu_pct', 24)
        if n:
            store = f'  ·  {n["n"]} samples/24h'
    else:
        store = '  ·  no metric store'
    p.L("foot2",
           f'<span font="{FXS}" foreground="{DIM}">up '
           f'{fmt_elapsed(si.get("uptime",0))}  ·  venv {si.get("venv","—")}'
           f'{store}  ·  {time.strftime("%H:%M:%S")}</span>')
