"""
PSI pressure gauges — CPU, I/O and memory stall pressure.

This section used to open with a large clock and the full date. Both are gone:
the desktop's own top bar already shows the time, and the right panel's footer
repeats it a third time. Three clocks on one screen is not information, and at
the top of a panel whose content already runs past the bottom of the display it
was the most expensive 105 px on offer.

What is left is the thing nothing else on the desktop reports: how much time
tasks are stalled waiting for CPU, I/O and memory.
"""

from .. import widgets as W
from ..window import *   # noqa: F401,F403


def build(p):
    prow = p.hbox(4)
    prow.set_margin_top(3)
    prow.set_margin_bottom(1)
    for nm in ('cpu', 'io', 'mem'):
        col = p.vbox(prow, expand=True)
        p.lbl(f"psi_{nm}_l", c=col)
        p.wid(f"psi_{nm}", W.Gauge(height=5), c=col)


def refresh(p, s):
    psi(p, s)


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
