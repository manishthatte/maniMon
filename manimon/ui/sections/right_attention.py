"""
The attention queue: everything that might need a human, worst first.

Both halves of one section live here: the widgets it creates and the code that
fills them.
"""

import time

from .. import widgets as W
from ..window import *   # noqa: F401,F403
from ... import collect as C

MAX_ATTN = 12

SEV_COL = {C.SEV_CRIT: W.CRIT, C.SEV_WARN: W.WARN,
           C.SEV_INFO: TEAL, C.SEV_OK: W.OK}


def build(p):
        # 1. Attention ---------------------------------------------------------
        p.head("⚠", "ATTENTION", RED)
        p.lbl("attn_none")
        for i in range(MAX_ATTN):
            ev = Gtk.EventBox()
            ev.set_visible_window(False)
            ev.add_events(Gdk.EventMask.BUTTON_PRESS_MASK |
                          Gdk.EventMask.ENTER_NOTIFY_MASK |
                          Gdk.EventMask.LEAVE_NOTIFY_MASK)
            lab = Gtk.Label()
            lab.set_xalign(0)
            lab.set_use_markup(True)
            lab.set_ellipsize(3)
            lab.set_margin_top(1)
            lab.set_margin_bottom(1)
            ev.add(lab)
            ev.connect("button-press-event",
                       lambda w, e, idx=i: on_click(p, w, e, idx))
            ev.connect("enter-notify-event", lambda w, e: on_enter(p, w, e))
            ev.connect("leave-notify-event", lambda w, e: on_leave(p, w, e))
            p._lbs[f"attn{i}"] = lab
            p._wid[f"attnrow{i}"] = ev
            p.box.pack_start(ev, False, False, 0)
        p.lbl("attn_hint")


def refresh(p, s):
    items = s.get('attention') or []
    p.vis("attn_none", not items)
    p.vis("attn_hint", bool(items))
    if not items:
        p.L("attn_none",
               f'<span font="{FATB}" foreground="{W.OK}">✓  nothing needs you</span>')
    else:
        crit = sum(1 for i in items if i['sev'] == C.SEV_CRIT)
        p.L("attn_hint",
               f'<span font="{FXS}" foreground="{DIM}">'
               f'  click a row to dismiss it'
               + (f'   ·   {crit} critical' if crit else '') + '</span>')
    p._attn_keys = {}
    for i in range(MAX_ATTN):
        if i < len(items):
            it = items[i]
            col = SEV_COL.get(it['sev'], WHITE)
            p._attn_keys[i] = it['key'] if it['ackable'] else None
            mark = '' if it['ackable'] else \
                   f'<span font="{FXS}" foreground="{DIM}">  ·</span>'
            p.L(f"attn{i}",
                   f'<span font="{FATB}" foreground="{col}">{it["icon"]}  </span>'
                   f'<span font="{FATT}" foreground="{WHITE}">{it["text"]}</span>'
                   + mark)
            p.vis(f"attnrow{i}", True)
        else:
            p.vis(f"attnrow{i}", False)
    if len(items) > MAX_ATTN:
        p.L("attn_hint",
               f'<span font="{FXS}" foreground="{DIM}">  click to dismiss  ·  '
               f'+{len(items)-MAX_ATTN} more not shown</span>')


def on_click(p, widget, event, idx):
    key = p._attn_keys.get(idx)
    if key:
        C.acknowledge(key)
        with p._lock:
            snap = dict(p.snap)
        snap['attention'] = C.attention(snap)
        with p._lock:
            p.snap = snap
        p.refresh(snap)
    return True


def on_enter(p, widget, event):
    win = widget.get_window()
    if win:
        win.set_cursor(Gdk.Cursor.new_from_name(widget.get_display(), "pointer"))
    return False


def on_leave(p, widget, event):
    win = widget.get_window()
    if win:
        win.set_cursor(None)
    return False
