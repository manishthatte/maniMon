"""
systemd units being watched, and recent journal errors.

Both halves of one section live here: the widgets it creates and the code that
fills them.
"""

import time

from .. import widgets as W
from ..window import *   # noqa: F401,F403


def build(p):
        # 8. Services & journal ----------------------------------------------------
        p.head("◆", "SERVICES  &  JOURNAL", CYAN)
        p.lbl("svc_row")
        p.lbl("jrn_row")
        for i in range(3):
            p.lbl(f"jrn{i}")


def refresh(p, s):
    svcs = s.get('services') or {}
    chunks = []
    for name, state in svcs.items():
        if state == 'active':
            dot, col = '●', W.OK
        elif state in ('inactive', 'dead'):
            dot, col = '○', DIM
        else:
            dot, col = '●', W.CRIT
        chunks.append(f'<span foreground="{col}">{dot}</span>'
                      f'<span foreground="{DIM}">{name[:5]}</span>')
    p.L("svc_row", f'<span font="{FXS}">' + '  '.join(chunks) + '</span>')
    j = s.get('journal') or {}
    e, w = j.get('errors', 0), j.get('warnings', 0)
    p.L("jrn_row",
           f'<span font="{FS}" foreground="{W.CRIT if e else DIM}">{e} err</span>'
           f'<span font="{FS}" foreground="{DIM}">  ·  </span>'
           f'<span font="{FS}" foreground="{W.WARN if w else DIM}">{w} warn</span>'
           f'<span font="{FXS}" foreground="{DIM}">  this boot</span>')
    rec = j.get('recent') or []
    for i in range(3):
        if i < len(rec):
            p.L(f"jrn{i}",
                   f'<span font="{FXS}" foreground="{DIM}">  {rec[i][:50]}</span>')
            p.vis(f"jrn{i}", True)
        else:
            p.vis(f"jrn{i}", False)
