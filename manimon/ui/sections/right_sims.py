"""
Running jobs, with parsed progress and an ETA.

Both halves of one section live here: the widgets it creates and the code that
fills them.
"""

import time

from .. import widgets as W
from ..window import *   # noqa: F401,F403
from ...util import fmt_bytes, fmt_elapsed

MAX_SIMS = 4


def build(p):
        # 2. Running simulations -----------------------------------------------
        p.head("▶", "RUNNING  SIMULATIONS", LIME)
        p.lbl("sim_none")
        for i in range(MAX_SIMS):
            b = p.vbox()
            p._wid[f"simbox{i}"] = b
            p.lbl(f"sim{i}_a", c=b)
            p.lbl(f"sim{i}_b", c=b)
            p.wid(f"sim{i}_bar", W.Gauge(height=6), c=b)


def refresh(p, s):
    sims = s.get('sims') or []
    p.vis("sim_none", not sims)
    if not sims:
        p.L("sim_none",
               f'<span font="{FS}" foreground="{DIM}">  no simulation running</span>')
    for i in range(MAX_SIMS):
        box = p._wid.get(f"simbox{i}")
        if i >= len(sims):
            if box:
                box.set_visible(False)
                box.set_no_show_all(True)
            continue
        if box:
            box.set_visible(True)
            box.set_no_show_all(False)
        sim = sims[i]
        p.L(f"sim{i}_a",
               f'<span font="{FB}" foreground="{LIME}">{sim["sim_id"][:16]}</span>'
               f'<span font="{FXS}" foreground="{DIM}">  {sim["comm"][:10]}'
               f'  pid {sim["pid"]}</span>'
               f'<span font="{FS}" foreground="{W.INK}">   {sim["cpu"]:.0f}%</span>'
               f'<span font="{FXS}" foreground="{DIM}">  {fmt_bytes(sim["rss"])}'
               f'  {sim["threads"]}t</span>')
        eta = (f'  ETA {fmt_elapsed(sim["eta"])}' if sim.get('eta') else '')
        prog = sim.get('progress') or '—'
        p.L(f"sim{i}_b",
               f'<span font="{FXS}" foreground="{TEAL}">  {fmt_elapsed(sim["elapsed"])}'
               f'{eta}</span>'
               f'<span font="{FXS}" foreground="{DIM}">   {prog[:44]}</span>')
        bar = p._wid.get(f"sim{i}_bar")
        if bar:
            if sim.get('frac'):
                bar.set(sim['frac'] * 100, W.CAT[0])
                bar.set_visible(True)
            else:
                bar.set(0, W.CAT[0])
