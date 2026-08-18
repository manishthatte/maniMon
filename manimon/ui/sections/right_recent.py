"""
Jobs that finished in the last day.

Both halves of one section live here: the widgets it creates and the code that
fills them.
"""

import time

from .. import widgets as W
from ..window import *   # noqa: F401,F403
from ...util import fmt_bytes, fmt_elapsed, fmt_age

MAX_RECENT = 5


def build(p):
        # 5. Recently finished ---------------------------------------------------
        p.head("✓", "RECENTLY  FINISHED  (24 h)", TEAL)
        p.lbl("recent_none")
        for i in range(MAX_RECENT):
            p.lbl(f"recent{i}")


def refresh(p, s):
    rs = s.get('recent') or []
    p.vis("recent_none", not rs)
    if not rs:
        p.L("recent_none",
               f'<span font="{FS}" foreground="{DIM}">  nothing finished today</span>')
    for i in range(MAX_RECENT):
        if i >= len(rs):
            p.vis(f"recent{i}", False)
            continue
        r = rs[i]
        ok = r['ok']
        col = W.OK if ok else W.CRIT
        mark = '✓' if ok else '✗'
        dur = f'  {fmt_elapsed(r["seconds"])}' if r.get('seconds') else ''
        p.L(f"recent{i}",
               f'<span font="{FS}" foreground="{col}">{mark}  </span>'
               f'<span font="{FS}" foreground="{WHITE}">{r["sim_id"][:18]}</span>'
               f'<span font="{FXS}" foreground="{DIM}">{dur}  '
               f'{fmt_bytes(r["size"])}  ·  {fmt_age(r["age"])}</span>')
        p.vis(f"recent{i}", True)
