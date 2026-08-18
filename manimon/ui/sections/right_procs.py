"""
Processes that are not recognised jobs but are using the machine.

Both halves of one section live here: the widgets it creates and the code that
fills them.
"""

import time

from .. import widgets as W
from ..window import *   # noqa: F401,F403
from ...util import fmt_bytes, fmt_elapsed

MAX_PROCS = 4


def build(p):
        # 7. Other processes -------------------------------------------------------
        p.head("≡", "OTHER  PROCESSES", GOLD)
        p.lbl("proc_hdr")
        for i in range(MAX_PROCS):
            p.lbl(f"proc{i}")


def refresh(p, s):
    procs = s.get('procs') or []
    sim_pids = {x['pid'] for x in (s.get('sims') or [])}
    others = [q for q in procs
              if q['pid'] not in sim_pids and q['elapsed'] >= 60
              and q['cpu'] >= 0.5][:MAX_PROCS]
    p.L("proc_hdr",
           f'<span font="{FXS}" foreground="{DIM}">'
           f'    PID   CPU%      RSS   elapsed  command</span>')
    for i in range(MAX_PROCS):
        if i >= len(others):
            p.vis(f"proc{i}", False)
            continue
        proc = others[i]
        ccol = W.CRIT if proc['cpu'] > 200 else (W.WARN if proc['cpu'] > 80 else WHITE)
        p.L(f"proc{i}",
               f'<span font="{FXS}">'
               f'<span foreground="{DIM}">{proc["pid"]:>7}</span>'
               f'<span foreground="{ccol}"> {proc["cpu"]:6.1f}</span>'
               f'<span foreground="{DIM}"> {fmt_bytes(proc["rss"]):>8}</span>'
               f'<span foreground="{TEAL}"> {fmt_elapsed(proc["elapsed"]):>9}</span>'
               f'  <span foreground="{WHITE}">{proc["comm"][:14]}</span></span>')
        p.vis(f"proc{i}", True)
