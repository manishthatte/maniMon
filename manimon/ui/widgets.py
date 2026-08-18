#!/usr/bin/env python3
"""
maniMon — Cairo widgets.

Colours live in `palette.py` — see that file for the three-rule colour policy
(magnitude / identity / state), the contrast measurements, and the CVD
simulation that chose the categorical set. Nothing colour-valued is declared
here; this module is marks and geometry only.

Author: Manish Jagdish Thatte
"""

import math
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Pango, PangoCairo

from .palette import (                                    # noqa: F401
    SURFACE, TRACK, RULE, INK, INK_DIM, CAT, SEQ, OK, WARN, CRIT,
    GLOSS_RGBA, HILITE_RGBA,
)


def rgb(h):
    return int(h[1:3], 16) / 255, int(h[3:5], 16) / 255, int(h[5:7], 16) / 255


def seq_color(frac):
    """Sequential ramp lookup, frac in [0,1]. Linear interpolation between steps."""
    frac = max(0.0, min(1.0, frac))
    x = frac * (len(SEQ) - 1)
    i = int(x)
    if i >= len(SEQ) - 1:
        return rgb(SEQ[-1])
    t = x - i
    a, b = rgb(SEQ[i]), rgb(SEQ[i + 1])
    return tuple(a[k] + (b[k] - a[k]) * t for k in range(3))


def status_color(pct, warn=85, crit=92):
    return CRIT if pct >= crit else (WARN if pct >= warn else OK)


def temp_color(t, warn=70, crit=88):
    return CRIT if t >= crit else (WARN if t >= warn else OK)


def _pill(cr, x, y, w, h, r=None):
    """Rounded rect. 4px radius on data ends per the mark spec."""
    if r is None:
        r = min(4.0, h / 2)
    r = max(0.0, min(r, w / 2, h / 2))
    if r <= 0.01:
        cr.rectangle(x, y, w, h)
        return
    cr.new_path()
    cr.arc(x + r,     y + r,     r, math.pi,        1.5 * math.pi)
    cr.arc(x + w - r, y + r,     r, -0.5 * math.pi, 0)
    cr.arc(x + w - r, y + h - r, r, 0,              0.5 * math.pi)
    cr.arc(x + r,     y + h - r, r, 0.5 * math.pi,  math.pi)
    cr.close_path()


def legend(cr, x, y, entries, size=9):
    """
    Swatch + label pairs. The swatch carries identity; the words stay in muted
    ink. Colouring legend text the series colour is the classic mistake — it
    makes text a data mark and drops contrast below readable.
    """
    for col, label in entries:
        if col:
            cr.set_source_rgb(*rgb(col))
            _pill(cr, x, y - 3.5, 7, 7, 2)
            cr.fill()
        else:
            cr.set_source_rgb(*rgb(RULE))
            cr.set_line_width(1)
            cr.rectangle(x + 0.5, y - 3, 6, 6)
            cr.stroke()
        x += 11
        x += _text(cr, x, y, label, size, INK_DIM) + 12
    return x


def _text(cr, x, y, s, size=9, color=INK, bold=False, align='left', width=None):
    """Pango text. Returns the rendered width."""
    layout = PangoCairo.create_layout(cr)
    desc = Pango.FontDescription(f"Monospace {'Bold ' if bold else ''}{size}")
    layout.set_font_description(desc)
    layout.set_text(s, -1)
    tw, th = layout.get_pixel_size()
    if align == 'right' and width is not None:
        x = x + width - tw
    elif align == 'center' and width is not None:
        x = x + (width - tw) / 2
    cr.set_source_rgb(*rgb(color))
    cr.move_to(x, y - th / 2)
    PangoCairo.show_layout(cr, layout)
    return tw


# ═══════════════════════════════════════════════════════════════════════════════
#  HeatGrid — one cell per logical CPU
# ═══════════════════════════════════════════════════════════════════════════════
class HeatGrid(Gtk.DrawingArea):
    """
    Square-ish grid of cells, colour = magnitude on the sequential ramp.

    `order` maps cell position -> data index, so SMT siblings can be placed
    adjacent: core k occupies cells 2k and 2k+1, and a saturated core reads as a
    solid 2-cell block rather than two cells scattered 32 apart.
    """
    GAP = 2

    def __init__(self, cols=8, rows=8, cell=22, order=None):
        super().__init__()
        self.cols, self.rows, self.cell = cols, rows, cell
        self.order = order or list(range(cols * rows))
        self._vals = [0.0] * (cols * rows)
        w = cols * cell + (cols - 1) * self.GAP
        h = rows * cell + (rows - 1) * self.GAP
        self.set_size_request(w, h)
        self.connect("draw", self._draw)

    def set_values(self, vals):
        self._vals = list(vals)
        self.queue_draw()

    def _draw(self, w, cr):
        c, g = self.cell, self.GAP
        for r in range(self.rows):
            for col in range(self.cols):
                cell_i = r * self.cols + col
                if cell_i >= len(self.order):
                    continue
                idx = self.order[cell_i]
                v = self._vals[idx] if idx < len(self._vals) else 0.0
                x, y = col * (c + g), r * (c + g)
                cr.set_source_rgb(*seq_color(v / 100.0))
                _pill(cr, x, y, c, c, 2)
                cr.fill()
                # A fully saturated thread earns a hairline so it pops at a glance
                if v >= 90:
                    cr.set_source_rgba(*HILITE_RGBA)
                    cr.set_line_width(1)
                    _pill(cr, x + 0.5, y + 0.5, c - 1, c - 1, 2)
                    cr.stroke()


class HeatLegend(Gtk.DrawingArea):
    """Gradient strip + end labels, so the ramp is never unexplained."""

    def __init__(self, height=9):
        super().__init__()
        self.set_size_request(-1, height + 12)
        self.connect("draw", self._draw)

    def _draw(self, w, cr):
        a = w.get_allocation()
        bw, bh = min(a.width - 62, 150), 7
        x0, y0 = 0, 2
        steps = 60
        for i in range(steps):
            cr.set_source_rgb(*seq_color(i / (steps - 1)))
            cr.rectangle(x0 + i * bw / steps, y0, bw / steps + 1, bh)
            cr.fill()
        _text(cr, x0 + bw + 6, y0 + bh / 2, "0", 9, INK_DIM)
        _text(cr, x0 + bw + 22, y0 + bh / 2, "→ 100% busy", 9, INK_DIM)


# ═══════════════════════════════════════════════════════════════════════════════
#  StackBar — composition of a whole
# ═══════════════════════════════════════════════════════════════════════════════
class StackBar(Gtk.DrawingArea):
    """
    Segmented bar. `segments` = [(fraction, colour), ...]; the remainder renders
    as recessive track. A 2px surface gap separates adjacent fills so segment
    boundaries survive without outlines.
    """
    def __init__(self, height=13):
        super().__init__()
        self._segs = []
        self.h = height
        self.set_size_request(-1, height + 4)
        self.connect("draw", self._draw)

    def set_segments(self, segs):
        self._segs = [(max(0.0, min(1.0, f)), c) for f, c in segs if f > 0.0005]
        self.queue_draw()

    def _draw(self, w, cr):
        a = w.get_allocation()
        W, H = a.width, a.height
        y = (H - self.h) / 2
        cr.set_source_rgb(*rgb(TRACK))
        _pill(cr, 0, y, W, self.h)
        cr.fill()
        x = 0.0
        for i, (frac, col) in enumerate(self._segs):
            seg_w = W * frac
            if seg_w < 1:
                continue
            draw_w = seg_w - (2 if i < len(self._segs) - 1 else 0)
            if draw_w <= 0:
                x += seg_w
                continue
            cr.set_source_rgb(*rgb(col))
            first, last = (i == 0), (abs(x + seg_w - W) < 1.5)
            if first or last:
                _pill(cr, x, y, draw_w, self.h)
            else:
                cr.rectangle(x, y, draw_w, self.h)
            cr.fill()
            x += seg_w


# ═══════════════════════════════════════════════════════════════════════════════
#  RowBar — label + inline bar + value, on ONE line
# ═══════════════════════════════════════════════════════════════════════════════
class RowBar(Gtk.DrawingArea):
    """
    The workhorse for dense lists (13 filesystems, top-memory processes).
    Everything on a single 15px row: name, bar, value, percentage.
    """
    def __init__(self, label_w=96, value_w=92, height=18, size=11):
        super().__init__()
        self.label_w, self.value_w, self.size = label_w, value_w, size
        self._label = self._value = ''
        self._pct = 0.0
        self._col = CAT[0]
        self._lcol = INK
        self.set_size_request(-1, height)
        self.connect("draw", self._draw)

    def set(self, label, value, pct, col=None, label_col=None):
        self._label, self._value = label, value
        self._pct = max(0.0, min(100.0, pct))
        self._col = col or CAT[0]
        self._lcol = label_col or INK
        self.queue_draw()

    def _draw(self, w, cr):
        a = w.get_allocation()
        W, H = a.width, a.height
        cy = H / 2
        _text(cr, 0, cy, self._label, self.size, self._lcol)
        bx = self.label_w
        bw = max(W - self.label_w - self.value_w - 8, 12)
        bh = 6
        by = cy - bh / 2
        cr.set_source_rgb(*rgb(TRACK))
        _pill(cr, bx, by, bw, bh, 3)
        cr.fill()
        if self._pct > 0.3:
            fw = max(bh, bw * self._pct / 100)
            cr.set_source_rgb(*rgb(self._col))
            _pill(cr, bx, by, fw, bh, 3)
            cr.fill()
        _text(cr, bx + bw + 8, cy, self._value, self.size, INK_DIM,
              align='right', width=self.value_w)


# ═══════════════════════════════════════════════════════════════════════════════
#  Sparklines
# ═══════════════════════════════════════════════════════════════════════════════
class Spark(Gtk.DrawingArea):
    """Single series over time. 2px line, soft fill, current-value dot."""
    def __init__(self, n=90, col=None, height=18, label=""):
        super().__init__()
        self.n = n
        self._data = [0.0] * n
        self._col = col or CAT[0]
        self._lbl = label
        self._fixed_max = None
        self.set_size_request(-1, height)
        self.connect("draw", self._draw)

    def push(self, v):
        self._data.append(float(v))
        self._data.pop(0)
        self.queue_draw()

    def set_col(self, c):
        self._col = c

    def set_max(self, m):
        self._fixed_max = m

    def _draw(self, w, cr):
        a = w.get_allocation()
        W, H, p = a.width, a.height, 2
        cr.set_source_rgb(*rgb(TRACK))
        _pill(cr, 0, p, W, H - 2 * p, 3)
        cr.fill()
        mx = self._fixed_max or max(max(self._data), 1.0)
        xs, ys = W - 2 * p, H - 2 * p - 1
        n = len(self._data)
        r, g, b = rgb(self._col)

        cr.move_to(p, p + ys)
        for i, v in enumerate(self._data):
            cr.line_to(p + i * xs / (n - 1), p + ys * (1 - min(v / mx, 1.0)))
        cr.line_to(p + xs, p + ys)
        cr.close_path()
        cr.set_source_rgba(r, g, b, 0.18)
        cr.fill()

        cr.set_line_width(2)
        cr.set_source_rgba(r, g, b, 0.95)
        for i, v in enumerate(self._data):
            x, y = p + i * xs / (n - 1), p + ys * (1 - min(v / mx, 1.0))
            cr.line_to(x, y) if i else cr.move_to(x, y)
        cr.stroke()

        lx = p + xs
        ly = p + ys * (1 - min(self._data[-1] / mx, 1.0))
        cr.set_source_rgb(r, g, b)
        cr.arc(lx, ly, 2.2, 0, 2 * math.pi)
        cr.fill()
        # No inline label: it collides with its own data, and the section
        # header already names the single series.


class DualSpark(Gtk.DrawingArea):
    """
    Network down/up as a mirrored pair on ONE shared scale.

    Deliberately not two independent y-scales: a dual-axis chart makes an
    80 Mb/s download and an 80 Kb/s upload look identical. Sharing the scale is
    the whole point — you can see which direction dominates.
    """
    def __init__(self, n=90, height=30):
        super().__init__()
        self.n = n
        self._dn = [0.0] * n
        self._up = [0.0] * n
        self.set_size_request(-1, height)
        self.connect("draw", self._draw)

    def push(self, dn, up):
        self._dn.append(float(dn)); self._dn.pop(0)
        self._up.append(float(up)); self._up.pop(0)
        self.queue_draw()

    def _draw(self, w, cr):
        a = w.get_allocation()
        W, H, p = a.width, a.height, 2
        cr.set_source_rgb(*rgb(TRACK))
        _pill(cr, 0, p, W, H - 2 * p, 3)
        cr.fill()
        mid = H / 2
        mx = max(max(self._dn), max(self._up), 1.0)
        xs = W - 2 * p
        half = (H - 2 * p) / 2 - 1
        n = self.n

        for data, col, sign in ((self._dn, CAT[0], -1), (self._up, CAT[1], 1)):
            r, g, b = rgb(col)
            cr.move_to(p, mid)
            for i, v in enumerate(data):
                cr.line_to(p + i * xs / (n - 1), mid + sign * half * min(v / mx, 1.0))
            cr.line_to(p + xs, mid)
            cr.close_path()
            cr.set_source_rgba(r, g, b, 0.22)
            cr.fill()
            cr.set_line_width(1.6)
            cr.set_source_rgba(r, g, b, 0.95)
            for i, v in enumerate(data):
                x, y = p + i * xs / (n - 1), mid + sign * half * min(v / mx, 1.0)
                cr.line_to(x, y) if i else cr.move_to(x, y)
            cr.stroke()

        cr.set_source_rgba(*rgb(RULE), 0.9)
        cr.set_line_width(1)
        cr.move_to(p, mid); cr.line_to(W - p, mid); cr.stroke()


class MultiSpark(Gtk.DrawingArea):
    """
    Several series over time on ONE shared scale.

    Never a second y-axis: two scales make an 88 °C line and a 46 °C line sit at
    the same height, which is exactly the comparison the chart exists to make.
    Lines only — stacked translucent fills turn to mud past one series. Callers
    draw a legend, since identity must not rest on colour alone.
    """
    def __init__(self, n=110, series=2, height=34, floor=None, ceil=None):
        super().__init__()
        self.n = n
        self._data = [[0.0] * n for _ in range(series)]
        self._cols = [CAT[i % len(CAT)] for i in range(series)]
        self._floor, self._ceil = floor, ceil
        # Samples actually pushed. Without this the untouched zeros in the
        # warm-up buffer set the axis minimum, and a 46-52 C band gets squashed
        # into the top 2px of a 0-52 scale.
        self._filled = 0
        self.set_size_request(-1, height)
        self.connect("draw", self._draw)

    def push(self, values):
        for i, v in enumerate(values):
            if i < len(self._data):
                self._data[i].append(float(v))
                self._data[i].pop(0)
        self._filled = min(self._filled + 1, self.n)
        self.queue_draw()

    def _live(self, data):
        """Only the portion of the ring buffer that holds real samples."""
        return data[-self._filled:] if self._filled else []

    def set_cols(self, cols):
        self._cols = list(cols)

    def _draw(self, w, cr):
        a = w.get_allocation()
        W, H, p = a.width, a.height, 2
        cr.set_source_rgb(*rgb(TRACK))
        _pill(cr, 0, p, W, H - 2 * p, 3)
        cr.fill()

        live = [self._live(d) for d in self._data]
        flat = [v for d in live for v in d]
        if len(flat) < 2:
            return
        lo = self._floor if self._floor is not None else min(flat)
        hi = self._ceil if self._ceil is not None else max(flat)
        if hi - lo < 1e-6:
            hi = lo + 1.0
        raw_lo, raw_hi = lo, hi
        pad = (hi - lo) * 0.12
        lo, hi = lo - pad, hi + pad

        xs, ys = W - 2 * p, H - 2 * p - 1
        span = max(len(live[0]) - 1, 1)
        for si, data in enumerate(live):
            if len(data) < 2:
                continue
            r, g, b = rgb(self._cols[si % len(self._cols)])
            cr.set_line_width(2)
            cr.set_source_rgba(r, g, b, 0.95)
            for i, v in enumerate(data):
                # right-align: newest sample always sits at the right edge
                x = p + xs - (len(data) - 1 - i) * xs / span
                y = p + ys * (1 - (v - lo) / (hi - lo))
                cr.line_to(x, y) if i else cr.move_to(x, y)
            cr.stroke()
            ly = p + ys * (1 - (data[-1] - lo) / (hi - lo))
            cr.set_source_rgb(r, g, b)
            cr.arc(p + xs, ly, 2.2, 0, 2 * math.pi)
            cr.fill()

        _text(cr, 4, p + 7, f"{raw_hi:.0f}", 8, INK_DIM)
        _text(cr, 4, H - p - 7, f"{raw_lo:.0f}", 8, INK_DIM)


# ═══════════════════════════════════════════════════════════════════════════════
#  Gauge — a value against its own limit
# ═══════════════════════════════════════════════════════════════════════════════
class Gauge(Gtk.DrawingArea):
    """Bar with a cap tick — power draw vs board limit, temp vs throttle point."""
    def __init__(self, height=11):
        super().__init__()
        self._pct = 0.0
        self._col = CAT[0]
        self._mark = None
        self.h = height
        self.set_size_request(-1, height + 4)
        self.connect("draw", self._draw)

    def set(self, pct, col=None, mark=None):
        self._pct = max(0.0, min(100.0, pct))
        self._col = col or CAT[0]
        self._mark = mark
        self.queue_draw()

    def _draw(self, w, cr):
        a = w.get_allocation()
        W, H = a.width, a.height
        y = (H - self.h) / 2
        cr.set_source_rgb(*rgb(TRACK))
        _pill(cr, 0, y, W, self.h)
        cr.fill()
        if self._pct > 0.3:
            cr.set_source_rgb(*rgb(self._col))
            _pill(cr, 0, y, max(self.h, W * self._pct / 100), self.h)
            cr.fill()
        if self._mark is not None:
            mx = W * max(0.0, min(1.0, self._mark / 100.0))
            cr.set_source_rgba(*rgb(INK), 0.55)
            cr.set_line_width(1.5)
            cr.move_to(mx, y - 1); cr.line_to(mx, y + self.h + 1)
            cr.stroke()


# ═══════════════════════════════════════════════════════════════════════════════
#  Self-test harness
# ═══════════════════════════════════════════════════════════════════════════════
def _demo():
    import random
    win = Gtk.Window(title="widgets self-test")
    win.set_default_size(420, 460)
    css = Gtk.CssProvider()
    css.load_from_data(f"* {{ background-color: {SURFACE}; }}".encode())
    Gtk.StyleContext.add_provider_for_screen(
        win.get_screen(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    box.set_margin_start(8); box.set_margin_end(8)
    box.set_margin_top(8); box.set_margin_bottom(8)

    order = []
    for c in range(32):
        order += [c, c + 32]
    hg = HeatGrid(8, 8, 22, order)
    hg.set_values([random.random() * 100 for _ in range(64)])
    box.pack_start(hg, False, False, 0)
    box.pack_start(HeatLegend(), False, False, 0)

    sb = StackBar()
    sb.set_segments([(0.30, CAT[0]), (0.45, CAT[1]), (0.08, CAT[2])])
    box.pack_start(sb, False, False, 0)

    for lbl, val, pct in (("/tmp", "76G/196G", 39), ("/home", "1.4T/7.6T", 19),
                          ("/var", "3.1G/491G", 1)):
        rb = RowBar()
        rb.set(lbl, val, pct, status_color(pct))
        box.pack_start(rb, False, False, 0)

    sp = Spark(label="cpu %")
    for _ in range(90):
        sp.push(random.random() * 60)
    box.pack_start(sp, False, False, 0)

    ds = DualSpark()
    for _ in range(90):
        ds.push(random.random() * 1e6, random.random() * 2e5)
    box.pack_start(ds, False, False, 0)

    gg = Gauge()
    gg.set(45 / 241 * 100, CAT[1], mark=100)
    box.pack_start(gg, False, False, 0)

    win.add(box)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()


if __name__ == '__main__':
    _demo()
