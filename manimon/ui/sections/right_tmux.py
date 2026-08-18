"""
tmux sessions and their panes.

Both halves of one section live here: the widgets it creates and the code that
fills them.
"""

import time

from .. import widgets as W
from ..window import *   # noqa: F401,F403

MAX_PANES = 8


def build(p):
        # 3. tmux ---------------------------------------------------------------
        p.head("▣", "TMUX  SESSIONS", GREEN)
        p.lbl("tmux_none")
        for i in range(MAX_PANES):
            p.lbl(f"tmux{i}_a")
            p.lbl(f"tmux{i}_b")


def refresh(p, s):
    panes = s.get('tmux') or []
    p.vis("tmux_none", not panes)
    if not panes:
        p.L("tmux_none",
               f'<span font="{FS}" foreground="{DIM}">  no tmux server running</span>')
    procs = {q['pid']: q for q in (s.get('procs') or [])}
    for i in range(MAX_PANES):
        if i >= len(panes):
            p.vis(f"tmux{i}_a", False)
            p.vis(f"tmux{i}_b", False)
            continue
        pane = panes[i]
        col = W.CRIT if pane['dead'] else (LIME if pane['active'] else GREEN)
        mark = '✗' if pane['dead'] else ('▸' if pane['attached'] else '·')
        pr = procs.get(pane['pid'])
        cpu = f'  {pr["cpu"]:.0f}%' if pr else ''
        p.L(f"tmux{i}_a",
               f'<span font="{FS}" foreground="{col}">{mark}  </span>'
               f'<span font="{FS}" foreground="{WHITE}">{pane["session"]}:'
               f'{pane["window_name"][:14]}</span>'
               f'<span font="{FXS}" foreground="{DIM}">  {pane["cmd"][:12]}{cpu}</span>')
        p.L(f"tmux{i}_b",
               f'<span font="{FXS}" foreground="{DIM}">     {pane["last_line"][:52]}</span>')
        p.vis(f"tmux{i}_a", True)
        p.vis(f"tmux{i}_b", bool(pane['last_line']))
