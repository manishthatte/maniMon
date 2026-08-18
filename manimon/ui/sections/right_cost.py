"""
What each run actually cost, from the per-run accounting tables.

Both halves of one section live here: the widgets it creates and the code that
fills them.
"""

import time

from .. import widgets as W
from ..window import *   # noqa: F401,F403
from ...util import fmt_bytes, fmt_elapsed

MAX_COST = 5

# Run-cost figures come from a SQL join over the whole history, so they are
# recomputed on their own slow cadence rather than on every 2 s repaint.
COST_EVERY = 30.0


def build(p):
        # 5b. What runs actually cost ---------------------------------------------
        p.head("◔", "RUN  COST  (7 d)", GOLD)
        p.lbl("cost_none")
        p.lbl("cost_hdr")
        for i in range(MAX_COST):
            p.lbl(f"cost{i}")
        p.lbl("cost_foot")


def refresh(p, s):
    """What each simulation actually cost.

    Exclusive figures (cpu-h, peak RSS) are the run's own. Wh and peak GPU
    junction are machine-wide over the run's window and are NOT divided
    between overlapping runs — the trailing marker says how much company a
    run had, because with CPU/GPU co-scheduling as the standing policy,
    overlap is normal rather than exceptional.
    """
    if p.runs is None or not p.runs.available:
        p.vis("cost_hdr", False)
        p.vis("cost_foot", False)
        for i in range(MAX_COST):
            p.vis(f"cost{i}", False)
        p.vis("cost_none", True)
        p.L("cost_none",
               f'<span font="{FS}" foreground="{DIM}">  no run history — '
               f'is manimon-metrics running?</span>')
        return

    now = time.time()
    if now - getattr(p, '_cost_at', 0) >= COST_EVERY or not hasattr(p, '_cost_rows'):
        p._cost_at = now
        try:
            rows = p.runs.list(days=7, limit=MAX_COST)
            p._cost_rows = [p.runs.enrich(r, p.stats) for r in rows]
        except Exception:
            p._cost_rows = []
    rows = p._cost_rows

    p.vis("cost_none", not rows)
    p.vis("cost_hdr", bool(rows))
    p.vis("cost_foot", bool(rows))
    if not rows:
        p.L("cost_none",
               f'<span font="{FS}" foreground="{DIM}">  nothing has run in 7 days</span>')
        for i in range(MAX_COST):
            p.vis(f"cost{i}", False)
        return

    p.L("cost_hdr",
           f'<span font="{FXS}" foreground="{DIM}">  '
           f'{"simulation":<17}{"wall":>6}{"cpu-h":>7}{"RSS":>7}{"jc":>5}{"Wh":>7}</span>')
    for i in range(MAX_COST):
        if i >= len(rows):
            p.vis(f"cost{i}", False)
            continue
        r = rows[i]
        name = (r.get('sim_id') or r.get('comm') or '?')[:17]
        wall = fmt_elapsed(r.get('wall') or 0)
        cpuh = f"{(r.get('cpu_sec') or 0) / 3600:.2f}"
        rss = fmt_bytes(r.get('rss_max') or 0)
        jc = r.get('gpu_junction_max')
        wh = r.get('wh')
        live = r.get('ended') is None
        # Shared figures are dimmed when they were not this run's alone —
        # the number is still true of the machine, just not of this run.
        alone = (r.get('concurrent') or 1) <= 1
        shcol = WHITE if alone else DIM
        jcs = f"{jc:.0f}" if jc else "—"
        whs = f"{wh:.1f}" if wh else "—"
        p.L(f"cost{i}",
               f'<span font="{FS}" foreground="{LIME if live else WHITE}">'
               f'  {name:<17}</span>'
               f'<span font="{FXS}" foreground="{TEAL}">{wall:>6}</span>'
               f'<span font="{FXS}" foreground="{W.INK}">{cpuh:>7}</span>'
               f'<span font="{FXS}" foreground="{DIM}">{rss:>7}</span>'
               f'<span font="{FXS}" foreground="{shcol}">{jcs:>5}{whs:>7}</span>'
               + (f'<span font="{FXS}" foreground="{DIM}">  ▶</span>' if live else '')
               + ('' if alone else f'<span font="{FXS}" foreground="{DIM}"> +{r["concurrent"]-1}</span>'))
        p.vis(f"cost{i}", True)
    p.L("cost_foot",
           f'<span font="{FXS}" foreground="{DIM}">  cpu-h and RSS are the '
           f'run\'s own · jc/Wh are machine-wide, not split (+n = shared)</span>')
