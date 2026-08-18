#!/usr/bin/env python3
"""
The docked panel window: geometry, strut reservation, the refresh loop, and the
small vocabulary of layout calls both panels are written in.

IMPORTANT: the caller must set os.environ['GDK_BACKEND'] = 'x11' BEFORE
importing this module, which means before any gi import.
"""

import math, os, time

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GLib  # noqa: F401 (re-exported for panels)

# ── Shared config ─────────────────────────────────────────────────────────────
SPARK_N   = 60
UPDATE_MS = 2000

# ── Palette — light parchment ─────────────────────────────────────────────────
# Defined once, in palette.py, and imported by both this module and widgets.py.
# Do not re-declare a colour here: `python3 palette.py` is the contrast check
# that keeps the scheme honest, and it can only check what it owns.
from .palette import (                                   # noqa: F401
    BG, CYAN, BLUE, GREEN, YELLOW, ORANGE, RED,
    PURPLE, LIME, GOLD, TEAL, ROSE,
    DIM, DIM2, DIM3, WHITE, GLOSS_RGBA,
)

# ── Fonts ──────────────────────────────────────────────────────────────────────
FONT = "Monospace"
FT   = f"{FONT} Bold 15"   # section title
FH   = f"{FONT} Bold 14"   # section header
FB   = f"{FONT} Bold 13"   # bold value
F    = f"{FONT} 13"        # normal value
FS   = f"{FONT} 13"        # secondary
FXS  = f"{FONT} 11"        # small — still a reading size, not a caption size
FG_  = f"{FONT} 12"        # GPU column
FATT = f"{FONT} 13"        # attention queue  — the panel's headline content
FATB = f"{FONT} Bold 13"
FCLK = f"{FONT} Bold 30"   # clock digits (left panel)
FDAT = f"{FONT} 13"        # clock date   (left panel)

def _rgb(h):
    return int(h[1:3],16)/255, int(h[3:5],16)/255, int(h[5:7],16)/255

def _rgba(h, a=1.0):
    r,g,b = _rgb(h); return r, g, b, a

# ── Cairo helpers ──────────────────────────────────────────────────────────────
def pill(cr, x, y, w, h, r=None):
    if r is None: r = h / 2
    r = min(r, w/2, h/2)
    cr.new_path()
    cr.arc(x+r,   y+r,   r,  math.pi,       1.5*math.pi)
    cr.arc(x+w-r, y+r,   r, -0.5*math.pi,   0)
    cr.arc(x+w-r, y+h-r, r,  0,             0.5*math.pi)
    cr.arc(x+r,   y+h-r, r,  0.5*math.pi,   math.pi)
    cr.close_path()

# ══════════════════════════════════════════════════════════════════════════════
#  PanelWindow — shared dock-window behaviour for both panels
# ══════════════════════════════════════════════════════════════════════════════
import sys
import threading as _threading

from . import widgets as WD
from ..collect import Collector, taskbar_reserved
from ..store import metrics as _metrics, runs as _runs


class PanelWindow(Gtk.Window):
    """
    Edge-docked, always-on-top, strut-reserving panel.

    Data collection runs on a worker thread and is published to the UI thread by
    swapping a snapshot dict under a lock — so a slow `git`, `tmux` or `ping`
    can never stall a repaint. Clicks are accepted (DOCK windows can receive
    button events even though they take no keyboard focus), which is what makes
    the attention queue dismissable by clicking.
    """
    WIDTH = 420
    ANCHOR = "LEFT"
    TOP_OFFSET = 0
    WANT = None                            # snapshot keys this panel renders
    RECORD = False                         # only ONE panel writes the metric store

    def __init__(self):
        super().__init__()
        # The desktop taskbar may already own this edge (dash-to-panel sits on
        # the RIGHT at 96px here). Sit inboard of it and reserve past it, or the
        # taskbar lands on top of the panel.
        self.EDGE = taskbar_reserved(self.ANCHOR)
        self.snap = {}
        self._lock = _threading.Lock()
        self._collector = Collector(want=self.WANT, record=self.RECORD)
        # Read-only views of the history, for panels that display statistics.
        #
        # The imports are at module scope on purpose. Wrapping them in try/except
        # here meant a renamed module produced `self.stats = None` and a panel
        # that quietly said "no metric store" while the store sat there, fine.
        # A missing database is a legitimate state and stays soft; a broken
        # import is a bug and must be loud.
        try:
            self.stats = _metrics.Reader()
        except Exception as e:
            print(f"panel: metric store unavailable — {e}", file=sys.stderr, flush=True)
            self.stats = None
        try:
            self.runs = _runs.RunReader()
        except Exception as e:
            print(f"panel: run history unavailable — {e}", file=sys.stderr, flush=True)
            self.runs = None

        scr = Gdk.Screen.get_default()
        panel_h = scr.get_height() - self.TOP_OFFSET
        self.set_default_size(self.WIDTH, panel_h)
        self.set_size_request(self.WIDTH, panel_h)
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)       # clicks yes, keyboard focus no
        self.stick()
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.set_keep_above(True)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK |
                        Gdk.EventMask.POINTER_MOTION_MASK)

        css = Gtk.CssProvider()
        css.load_from_data(f"""
            * {{ background-color: {BG}; }}
            separator {{ background-color: {DIM3}; min-height: 1px; margin: 0; }}
            scrollbar {{ min-width: 4px; }}
            scrollbar slider {{ background-color: {DIM3}; border-radius: 2px; }}
        """.encode())
        Gtk.StyleContext.add_provider_for_screen(
            scr, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.add(scroll)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.box.set_margin_start(8)
        self.box.set_margin_end(8)
        self.box.set_margin_top(3)
        self.box.set_margin_bottom(6)
        scroll.add(self.box)

        self._lbs, self._brs, self._wid = {}, {}, {}
        self.connect("realize", self._on_realize)
        self._place()
        self.build()

        _threading.Thread(target=self._worker, daemon=True).start()
        GLib.timeout_add(UPDATE_MS, self._tick)
        self.show_all()

    # ── window placement / strut ──────────────────────────────────────────────
    def _place(self):
        scr = Gdk.Screen.get_default()
        disp = Gdk.Display.get_default()
        mon = disp.get_primary_monitor() or disp.get_monitor(0)
        geo = mon.get_geometry()
        x = (geo.x + self.EDGE if self.ANCHOR == "LEFT"
             else geo.x + geo.width - self.WIDTH - self.EDGE)
        self.move(x, self.TOP_OFFSET)

    def _on_realize(self, *_):
        GLib.timeout_add(500, self._apply_strut)
        GLib.timeout_add(2500, self._apply_strut)

    def _apply_strut(self):
        gw = self.get_window()
        if not gw:
            return False
        scr = Gdk.Screen.get_default()
        h = scr.get_height()
        sw = scr.get_width()
        W = self.WIDTH
        self._place()
        self.resize(W, h - self.TOP_OFFSET)
        xid = gw.get_xid()
        disp = os.environ.get("DISPLAY", ":0")
        R = W + self.EDGE          # reserve past the taskbar, not under it
        if self.ANCHOR == "LEFT":
            strut = f"{R},0,0,0"
            strut_p = f"{R},0,0,0,{self.TOP_OFFSET},{h-1},0,0,0,0,0,0"
        else:
            strut = f"0,{R},0,0"
            strut_p = f"0,{R},0,0,0,0,{self.TOP_OFFSET},{h-1},0,0,0,0"
        os.system(f'xprop -display {disp} -id {xid} -f _NET_WM_WINDOW_TYPE 32a '
                   f'-set _NET_WM_WINDOW_TYPE _NET_WM_WINDOW_TYPE_DOCK 2>/dev/null')
        os.system(f'xprop -display {disp} -id {xid} -f _NET_WM_STRUT 32c '
                   f'-set _NET_WM_STRUT "{strut}" 2>/dev/null')
        os.system(f'xprop -display {disp} -id {xid} -f _NET_WM_STRUT_PARTIAL 32c '
                   f'-set _NET_WM_STRUT_PARTIAL "{strut_p}" 2>/dev/null')
        print(f"{self.ANCHOR} strut {R}px (panel {W} + taskbar {self.EDGE}) on {sw}x{h}  xid={xid}", flush=True)
        return False

    # ── data plumbing ─────────────────────────────────────────────────────────
    def _worker(self):
        first = True
        while True:
            try:
                snap = self._collector.tick(force_all=first)
                first = False
                with self._lock:
                    self.snap = dict(snap)
            except Exception as e:
                print(f"collector error: {e}", flush=True)
            time.sleep(UPDATE_MS / 1000.0)

    def _tick(self):
        with self._lock:
            snap = self.snap
        if snap:
            try:
                self.refresh(snap)
            except Exception as e:
                import traceback
                print(f"refresh error: {e}", flush=True)
                traceback.print_exc()
        return True

    # ── subclass hooks ────────────────────────────────────────────────────────
    def build(self):
        raise NotImplementedError

    def refresh(self, snap):
        raise NotImplementedError

    # ── layout helpers ────────────────────────────────────────────────────────
    def lbl(self, key, mt=0, ms=0, c=None):
        l = Gtk.Label()
        l.set_xalign(0)
        l.set_use_markup(True)
        l.set_ellipsize(3)                      # PANGO_ELLIPSIZE_END
        if mt: l.set_margin_top(mt)
        if ms: l.set_margin_start(ms)
        self._lbs[key] = l
        (c or self.box).pack_start(l, False, False, 0)
        return l

    def wid(self, key, widget, mt=0, mb=0, c=None):
        widget.set_margin_top(mt)
        widget.set_margin_bottom(mb)
        self._wid[key] = widget
        (c or self.box).pack_start(widget, False, False, 0)
        return widget

    def L(self, key, markup):
        if key in self._lbs:
            self._lbs[key].set_markup(markup)

    def vis(self, key, on):
        for d in (self._lbs, self._brs, self._wid):
            if key in d:
                d[key].set_visible(on)
                d[key].set_no_show_all(not on)

    def head(self, icon, text, col=CYAN, c=None):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_margin_top(3)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        row.set_margin_bottom(2)
        acc = Gtk.DrawingArea()
        acc.set_size_request(3, -1)
        rc, gc, bc = _rgb(col)

        def _da(w, cr, r=rc, g=gc, b=bc):
            ah = w.get_allocation().height
            cr.set_source_rgba(r, g, b, 1)
            pill(cr, 0, 2, 3, max(ah - 4, 2), 1)
            cr.fill()

        acc.connect("draw", _da)
        row.pack_start(acc, False, False, 0)
        lbl = Gtk.Label()
        safe = GLib.markup_escape_text(f'{icon}  {text}')
        lbl.set_markup(f'<span font="{FH}" foreground="{col}">{safe}</span>')
        lbl.set_xalign(0)
        lbl.set_margin_start(3)
        row.pack_start(lbl, True, True, 0)
        outer.pack_start(row, False, False, 0)
        sep = Gtk.Separator()
        sep.set_margin_bottom(3)
        outer.pack_start(sep, False, False, 0)
        (c or self.box).pack_start(outer, False, False, 0)
        return outer

    def hbox(self, spacing=6, c=None):
        b = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing)
        (c or self.box).pack_start(b, False, False, 0)
        return b

    def vbox(self, c=None, spacing=0, expand=False):
        """
        Vertical container. expand defaults to FALSE: an expandable child in the
        main column soaks up every spare pixel, which opened a 400px hole where
        a hidden GPU block used to be. Pass expand=True only inside an hbox,
        where it means "take the leftover width".
        """
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
        (c or self.box).pack_start(b, expand, expand, 0)
        return b

    def title_bar(self, text, col=None):
        stripe = Gtk.DrawingArea()
        stripe.set_size_request(-1, 3)

        def _stripe(w, cr):
            a = w.get_allocation()
            cols = WD.CAT + [WD.OK, WD.WARN, WD.CRIT]
            step = a.width / len(cols)
            for i, cc in enumerate(cols):
                cr.set_source_rgb(*WD.rgb(cc))
                cr.rectangle(i * step, 0, step + 1, 3)
                cr.fill()

        stripe.connect("draw", _stripe)
        self.box.pack_start(stripe, False, False, 0)
        t = Gtk.Label()
        t.set_markup(f'<span font="{FT}" foreground="{col or GOLD}">{text}</span>')
        t.set_xalign(0.5)
        t.set_margin_top(3)
        t.set_margin_bottom(2)
        self.box.pack_start(t, False, False, 0)
        self.box.pack_start(Gtk.Separator(), False, False, 0)


# ── Explicit exports (includes _-prefixed helpers) ────────────────────────────
__all__ = [
    'PanelWindow', 'WD',
    # config
    'SPARK_N', 'UPDATE_MS',
    # palette
    'BG', 'CYAN', 'BLUE', 'GREEN', 'YELLOW', 'ORANGE', 'RED',
    'PURPLE', 'LIME', 'GOLD', 'TEAL', 'ROSE', 'DIM', 'DIM2', 'DIM3', 'WHITE',
    # fonts
    'FONT', 'FT', 'FH', 'FB', 'F', 'FS', 'FXS', 'FG_', 'FATT', 'FATB',
    'FCLK', 'FDAT',
    # colour helpers
    '_rgb', '_rgba',
    # cairo
    'pill',
    # gi re-exports
    'Gtk', 'Gdk', 'GLib',
]
