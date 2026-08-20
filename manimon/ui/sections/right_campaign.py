"""
An optional campaign of batch runs on disk.

Both halves of one section live here: the widgets it creates and the code that
fills them.
"""

import time

from .. import widgets as W
from ..window import *   # noqa: F401,F403
from ...config import CAMPAIGN_LABEL, LAYERS


def build(p):
        # 4. Campaign -----------------------------------------------------------
        p.head("◈", CAMPAIGN_LABEL, PURPLE)
        p.lbl("camp_hdr")
        p.wid("camp_legend", Gtk.DrawingArea())
        p._wid["camp_legend"].set_size_request(-1, 14)
        p._wid["camp_legend"].connect("draw",
                                  lambda w, cr: draw_legend(p, w, cr))
        for i, lay in enumerate(LAYERS):
            row = p.hbox(4)
            p._wid[f"camprow{i}"] = row
            l = Gtk.Label()
            l.set_markup("")
            l.set_xalign(0)
            l.set_size_request(26, -1)
            p._lbs[f"camp{i}_l"] = l
            row.pack_start(l, False, False, 0)
            bar = W.StackBar(height=9)
            p._wid[f"camp{i}_bar"] = bar
            row.pack_start(bar, True, True, 0)
            v = Gtk.Label()
            v.set_xalign(1)
            v.set_size_request(84, -1)
            p._lbs[f"camp{i}_v"] = v
            row.pack_start(v, False, False, 0)
        p.lbl("camp_foot")


def refresh(p, s):
    c = s.get('campaign') or {}
    if not c:
        return
    scripts = max(c.get('scripts', 0), 1)
    conf, part = c.get('confirmed', 0), c.get('partial', 0)
    p.L("camp_hdr",
           f'<span font="{FB}" foreground="{W.INK}">{conf}</span>'
           f'<span font="{FS}" foreground="{DIM}"> / {scripts} confirmed</span>'
           f'<span font="{FS}" foreground="{W.CAT[1]}">   {part} partial</span>'
           f'<span font="{FS}" foreground="{DIM}">  {c.get("pending",0)} pend</span>'
           f'<span font="{FXS}" foreground="{DIM}">  ·  {c.get("ran",0)} ran</span>')
    for i, lay in enumerate(LAYERS):
        row = next((l for l in c.get('layers', []) if l['layer'] == lay), None)
        if not row or not row['scripts']:
            p._wid[f"camprow{i}"].set_visible(False)
            p._wid[f"camprow{i}"].set_no_show_all(True)
            continue
        p._wid[f"camprow{i}"].set_visible(True)
        p._wid[f"camprow{i}"].set_no_show_all(False)
        n = row['scripts']
        p._lbs[f"camp{i}_l"].set_markup(
            f'<span font="{FXS}" foreground="{DIM}">{lay}</span>')
        p._wid[f"camp{i}_bar"].set_segments([
            (row['confirmed'] / n, W.CAT[0]),
            (row['partial'] / n, W.CAT[1]),
        ])
        flag = ('<span foreground="%s">  ⚠</span>' % W.WARN) if row['unbacked'] else ''
        p._lbs[f"camp{i}_v"].set_markup(
            f'<span font="{FXS}" foreground="{DIM}">{row["confirmed"]}'
            f'+{row["partial"]}/{n}</span>{flag}')
    failed = c.get('failed', 0)
    if failed:
        foot = (f'<span font="{FXS}" foreground="{W.CRIT}">  {failed} recorded '
                f'failure(s)</span>')
    elif c.get('have_ledger'):
        foot = (f'<span font="{FXS}" foreground="{DIM}">  ⚠ = ledger credits it, '
                f'disk has nothing</span>')
    else:
        # No scoreboard to check disk against. Say so — an unlabelled bar here
        # reads as "confirmed", which is a stronger claim than runs on disk.
        foot = (f'<span font="{FXS}" foreground="{DIM}">  no ledger — bars are '
                f'runs on disk, not confirmations</span>')
    p.L("camp_foot", foot)


def draw_legend(p, widget, cr):
    have = bool(((getattr(p, 'snap', None) or {}).get('campaign')
                 or {}).get('have_ledger'))
    W.legend(cr, 26, widget.get_allocation().height / 2,
             [(W.CAT[0], "confirmed"), (W.CAT[1], "partial"), (None, "pending")]
             if have else
             [(W.CAT[0], "ran"), (None, "no artefacts")])
    return True
