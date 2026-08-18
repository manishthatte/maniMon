"""
Clock, date and PSI pressure gauges.

Both halves of one section live here: the widgets it creates and the code that
fills them. They used to sit 150 lines apart in a single 850-line module.
"""

import time

from .. import widgets as W
from ..window import *   # noqa: F401,F403
from ...util import fmt_bytes, fmt_rate, fmt_elapsed

def build(p):
        # 1. Clock + pressure ------------------------------------------------
        p.lbl("clock", mt=2)
        p.lbl("date")
        prow = p.hbox(4)
        prow.set_margin_top(3)
        prow.set_margin_bottom(1)
        for nm in ('cpu', 'io', 'mem'):
            col = p.vbox(prow, expand=True)
            p.lbl(f"psi_{nm}_l", c=col)
            p.wid(f"psi_{nm}", W.Gauge(height=5), c=col)


def refresh(p, s):
    psi(p, s)
    p.L("clock", f'<span font="{FCLK}" foreground="{GOLD}">'
                    f'{time.strftime("%H:%M:%S")}</span>')
    p.L("date", f'<span font="{FDAT}" foreground="{DIM}">'
                   f'  {time.strftime("%A,  %d %B %Y")}</span>')


def psi(p, s):
    pressure = s.get('pressure', {})
    for nm, key in (('cpu', 'cpu'), ('io', 'io'), ('mem', 'memory')):
        v = pressure.get(key, {}).get('some_avg10', 0.0)
        col = W.CRIT if v >= 40 else (W.WARN if v >= 10 else W.CAT[0])
        p.L(f"psi_{nm}_l",
               f'<span font="{FXS}" foreground="{DIM}">{nm.upper()} </span>'
               f'<span font="{FXS}" foreground="{col}">{v:.0f}%</span>')
        g = p._wid.get(f"psi_{nm}")
        if g:
            g.set(min(v, 100), col)
