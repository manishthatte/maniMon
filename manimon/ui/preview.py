#!/usr/bin/env python3
"""
Render a still preview of the panel colour scheme to a PNG — no GTK, no X.

    python3 preview_theme.py [out.png]

The point is to judge the palette before committing to it, without restarting
the live panels. It draws the marks that actually carry data — heat grid,
stacked bar, row bars, sparkline, status text, the sequential ramp and the
categorical set — using the real values from palette.py.

Author: Manish Jagdish Thatte
"""

import math
import os
import sys

import cairo

from .palette import (PAGE, TRACK, RULE, INK, INK_DIM, CAT, SEQ,
                     CYAN, GREEN, YELLOW, ORANGE, RED, PURPLE, TEAL, GOLD,
                     OK, WARN, CRIT, GLOSS_RGBA, HILITE_RGBA)

W, H = 420, 900


def rgb(h):
    return int(h[1:3], 16) / 255, int(h[3:5], 16) / 255, int(h[5:7], 16) / 255


def seq_color(frac):
    frac = max(0.0, min(1.0, frac))
    x = frac * (len(SEQ) - 1)
    i = int(x)
    if i >= len(SEQ) - 1:
        return rgb(SEQ[-1])
    t = x - i
    a, b = rgb(SEQ[i]), rgb(SEQ[i + 1])
    return tuple(a[k] + (b[k] - a[k]) * t for k in range(3))


def pill(cr, x, y, w, h, r=None):
    if r is None:
        r = min(4.0, h / 2)
    r = max(0.0, min(r, w / 2, h / 2))
    if r <= 0.01:
        cr.rectangle(x, y, w, h)
        return
    cr.new_path()
    cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
    cr.arc(x + w - r, y + r, r, -0.5 * math.pi, 0)
    cr.arc(x + w - r, y + h - r, r, 0, 0.5 * math.pi)
    cr.arc(x + r, y + h - r, r, 0.5 * math.pi, math.pi)
    cr.close_path()


def text(cr, x, y, s, size=12, col=INK, bold=False):
    cr.select_font_face("Monospace",
                        cairo.FONT_SLANT_NORMAL,
                        cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(size)
    cr.set_source_rgb(*rgb(col))
    cr.move_to(x, y)
    cr.show_text(s)
    return cr.text_extents(s)[4]


def head(cr, y, icon, label, col=CYAN):
    text(cr, 12, y, f"{icon} {label}", 13, col, bold=True)
    cr.set_source_rgb(*rgb(RULE))
    cr.set_line_width(1)
    cr.move_to(12, y + 6)
    cr.line_to(W - 12, y + 6)
    cr.stroke()
    return y + 22


def rowbar(cr, y, label, value, pct, col):
    text(cr, 12, y + 4, label, 11, INK_DIM)
    bx, bw = 118, 190
    cr.set_source_rgb(*rgb(TRACK))
    pill(cr, bx, y - 6, bw, 12, 3)
    cr.fill()
    if pct > 0:
        cr.set_source_rgb(*rgb(col))
        pill(cr, bx, y - 6, max(6, bw * pct / 100), 12, 3)
        cr.fill()
        cr.set_source_rgba(*GLOSS_RGBA)
        pill(cr, bx, y - 6, max(6, bw * pct / 100), 5, 2)
        cr.fill()
    text(cr, bx + bw + 10, y + 4, value, 11, INK)
    return y + 20


def main(out="theme_preview.png"):
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, W, H)
    cr = cairo.Context(surf)
    cr.set_source_rgb(*rgb(PAGE))
    cr.paint()

    y = 34
    text(cr, 12, y, "03:42:15", 30, INK, bold=True)
    text(cr, 210, y - 10, "Tue 18 Aug 2026", 11, INK_DIM)
    text(cr, 210, y + 6, "up 3h 19m", 11, INK_DIM)
    y += 26

    # PSI strip
    for i, (lbl, v, c) in enumerate([("cpu", 4, GREEN), ("io", 31, YELLOW), ("mem", 0, GREEN)]):
        x = 12 + i * 132
        text(cr, x, y, lbl, 10, INK_DIM)
        cr.set_source_rgb(*rgb(TRACK))
        pill(cr, x + 30, y - 8, 88, 10, 3)
        cr.fill()
        cr.set_source_rgb(*rgb(c))
        pill(cr, x + 30, y - 8, max(4, 88 * v / 100), 10, 3)
        cr.fill()
    y += 26

    # ── CPU heat grid ────────────────────────────────────────────────────────
    y = head(cr, y, "▩", "CPU · EPYC 9334 · 32C/64T")
    vals = [3, 1, 0, 0, 12, 8, 0, 2, 96, 94, 71, 68, 44, 40, 22, 18,
            5, 3, 0, 0, 61, 58, 33, 30, 88, 91, 12, 9, 0, 1, 4, 2,
            0, 0, 2, 1, 47, 43, 15, 12, 99, 97, 55, 52, 7, 5, 0, 0,
            26, 23, 0, 0, 3, 2, 78, 74, 36, 32, 11, 8, 0, 0, 1, 0]
    cell, gap = 21, 3
    gx, gy = 12, y
    for r in range(8):
        for c in range(8):
            v = vals[r * 8 + c]
            cr.set_source_rgb(*seq_color(v / 100.0))
            pill(cr, gx + c * (cell + gap), gy + r * (cell + gap), cell, cell, 2)
            cr.fill()
            if v >= 90:
                cr.set_source_rgba(*HILITE_RGBA)
                cr.set_line_width(1)
                pill(cr, gx + c * (cell + gap) + .5, gy + r * (cell + gap) + .5,
                     cell - 1, cell - 1, 2)
                cr.stroke()
    # aggregate column beside the grid
    ax = gx + 8 * (cell + gap) + 14
    text(cr, ax, gy + 12, "24.8%", 17, INK, bold=True)
    text(cr, ax, gy + 32, "usr 21.4  sys 3.1", 10, INK_DIM)
    text(cr, ax, gy + 48, "Tctl   58.2 C", 11, ORANGE)
    text(cr, ax, gy + 64, "CCD  47/46/44/45", 10, INK_DIM)
    text(cr, ax, gy + 82, "2841 MHz avg", 11, INK)
    text(cr, ax, gy + 98, "load 12.4 8.1 5.2", 10, INK_DIM)
    text(cr, ax, gy + 116, "pkg 214 W", 11, GOLD)
    text(cr, ax, gy + 134, "ECC ce 0  ue 0", 10, GREEN)
    y = gy + 8 * (cell + gap) + 14

    # sequential ramp legend
    for i in range(60):
        cr.set_source_rgb(*seq_color(i / 59.0))
        cr.rectangle(12 + i * 3.1, y, 3.2, 8)
        cr.fill()
    text(cr, 12 + 60 * 3.1 + 8, y + 7, "0 → 100%", 10, INK_DIM)
    y += 26

    # ── GPU ──────────────────────────────────────────────────────────────────
    y = head(cr, y, "◉", "GPU · Radeon Pro W7900")
    y = rowbar(cr, y, "busy", "63%", 63, CAT[0])
    y = rowbar(cr, y, "VRAM", "28.4/45 G", 63, CAT[2])
    text(cr, 12, y + 4, "edge 61  junc 78  mem 82  vr 71/68/66", 10, INK_DIM)
    y += 18
    text(cr, 12, y + 4, "213 W / 241 W", 11, ORANGE)
    text(cr, 150, y + 4, "fan 61%", 11, INK_DIM)
    text(cr, 250, y + 4, "not throttled", 11, GREEN)
    y += 24

    # ── Memory stacked bar ───────────────────────────────────────────────────
    y = head(cr, y, "▦", "MEMORY · 251 GB DDR5")
    segs = [(38, CAT[0], "used"), (2, CAT[1], "buf"), (24, CAT[2], "cache"), (36, TRACK, "free")]
    x = 12
    for pct, col, _ in segs:
        w = (W - 24) * pct / 100
        cr.set_source_rgb(*rgb(col))
        cr.rectangle(x, y, w, 14)
        cr.fill()
        x += w
    y += 22
    lx = 12
    for pct, col, lbl in segs:
        cr.set_source_rgb(*rgb(col))
        pill(cr, lx, y - 6, 8, 8, 2)
        cr.fill()
        lx += 12
        lx += text(cr, lx, y, f"{lbl} {pct}%", 10, INK_DIM) + 14
    y += 22
    text(cr, 12, y, "4 of 8 channels populated · 153 of 461 GB/s", 11, WARN)
    y += 24

    # ── Storage ──────────────────────────────────────────────────────────────
    y = head(cr, y, "◈", "STORAGE")
    text(cr, 12, y, "nvme0n1  1.7T  52 C  r 14M/s  w 0M/s  wear 2%", 10, INK_DIM)
    y += 18
    y = rowbar(cr, y, "  /", "8.5/92 G", 9, CAT[3])
    y = rowbar(cr, y, "  /var", "1.3/37 G", 3, CAT[3])
    text(cr, 12, y, "sda  7.0T  34 C  ·  247 TB written", 10, INK_DIM)
    y += 18
    y = rowbar(cr, y, "  /home", "1.5/7.0 T", 21, CAT[3])
    y += 6

    # ── Status / attention rows ──────────────────────────────────────────────
    y = head(cr, y, "⚠", "ATTENTION", col=RED)
    for icon, msg, col in [("✗", "L0-07 berry_phase FAILED rc=1 · 3h ago", CRIT),
                           ("⏏", "BACKUP_DRIVE not mounted", WARN),
                           ("◫", "/tmp 88% full", WARN),
                           ("✓", "T1_D02 finished in 4h12m · 18m ago", OK)]:
        text(cr, 14, y + 4, icon, 12, col)
        text(cr, 34, y + 4, msg, 11, INK)
        y += 20
    y += 6

    # ── Sparkline ────────────────────────────────────────────────────────────
    y = head(cr, y, "◊", "THERMAL TREND · 24 h")
    data = [40 + 30 * abs(math.sin(i / 9.0)) + 6 * math.sin(i / 2.3) for i in range(90)]
    bx, bw, bh = 12, W - 24, 44
    cr.set_source_rgb(*rgb(TRACK))
    pill(cr, bx, y, bw, bh, 3)
    cr.fill()
    r, g, b = rgb(ORANGE)
    cr.move_to(bx, y + bh)
    for i, v in enumerate(data):
        cr.line_to(bx + i * bw / (len(data) - 1), y + bh - bh * (v / 100.0))
    cr.line_to(bx + bw, y + bh)
    cr.close_path()
    cr.set_source_rgba(r, g, b, 0.22)
    cr.fill()
    cr.set_line_width(1.4)
    cr.set_source_rgba(r, g, b, 0.95)
    for i, v in enumerate(data):
        x2, y2 = bx + i * bw / (len(data) - 1), y + bh - bh * (v / 100.0)
        cr.line_to(x2, y2) if i else cr.move_to(x2, y2)
    cr.stroke()
    y += bh + 18
    text(cr, 12, y, "GPU junction  p95 78 C  max 91 C  >85 C for 4% of 24h", 10, INK_DIM)

    surf.write_to_png(out)
    print(f"wrote {out}  ({W}x{H})")
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "theme_preview.png"))
