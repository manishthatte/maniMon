"""
Git working-tree state and backup freshness.

Both halves of one section live here: the widgets it creates and the code that
fills them.
"""

import time

from .. import widgets as W
from ..window import *   # noqa: F401,F403
from ..markup import kv
from ...util import fmt_age


def build(p):
        # 6. Repo & backups -------------------------------------------------------
        p.head("⎇", "REPO  &  BACKUPS", ROSE)
        p.lbl("git1")
        p.lbl("git2")
        for i in range(2):
            p.lbl(f"bkp{i}")


def refresh(p, s):
    g = s.get('repo') or {}
    dcol = W.WARN if g.get('dirty') else W.OK
    ucol = W.WARN if g.get('unpushed') else W.OK
    p.L("git1",
           f'<span font="{FS}" foreground="{DIM}">⎇ </span>'
           f'<span font="{FB}" foreground="{CYAN}">{g.get("branch","?")}</span>'
           + kv("   dirty", f'{g.get("dirty",0)}', dcol)
           + kv("   unpushed", f'{g.get("unpushed",0)}', ucol))
    p.L("git2",
           f'<span font="{FXS}" foreground="{DIM}">  {g.get("last_hash","")}  '
           f'{g.get("last_subject","")}  ·  {g.get("last_when","")}</span>')
    bks = s.get('backups') or []
    for i in range(2):
        if i >= len(bks):
            p.vis(f"bkp{i}", False)
            continue
        b = bks[i]
        if not b['mounted']:
            col, txt = W.WARN, 'not mounted'
        elif b['ok'] is False:
            col, txt = W.CRIT, 'FAILED'
        elif b['stale']:
            col, txt = W.WARN, f'stale · {fmt_age(b["age"])}'
        else:
            col, txt = W.OK, f'{fmt_age(b["age"])}'
        p.L(f"bkp{i}",
               f'<span font="{FS}" foreground="{col}">⏏ </span>'
               f'<span font="{FS}" foreground="{WHITE}">{b["label"]}</span>'
               f'<span font="{FXS}" foreground="{col}">  {txt}</span>'
               f'<span font="{FXS}" foreground="{DIM}">  {b["duration"]}</span>')
        p.vis(f"bkp{i}", True)
