#!/usr/bin/env python3
"""
maniMon — LEFT PANEL: the machine.

Every hardware subsystem, at a glance, top to bottom in glance-frequency order:

  Clock + PSI pressure · CPU (64-thread heat map) · GPU · Memory ·
  Storage (every disk and partition, + I/O trend) · Thermal & power

NETWORK lives on the RIGHT panel: at 1440 px tall this one could not hold it
and still be readable, and the right panel had the room.

Each section is a module under `sections/`, owning both the widgets it creates
and the code that fills them. This file is only the frame: which sections, in
what order, and the statistics helpers they share.

Colour policy lives in widgets.py: magnitude uses the sequential amber ramp,
composition uses the validated categorical trio, state uses the reserved
status palette — always alongside a number or glyph, never colour alone.

Author: Manish Jagdish Thatte
"""

import os
os.environ['GDK_BACKEND'] = 'x11'
os.environ.setdefault('DISPLAY', ':0')

import time

from .window import *                   # noqa: F401,F403
from . import widgets as W
from .sections import (left_clock, left_cpu, left_gpu, left_memory,
                       left_storage, left_chassis, left_power, left_footer)

# Order is glance frequency, not hardware hierarchy: the things looked at most
# often sit highest, where they can be read without moving your eyes.
SECTIONS = (left_clock, left_cpu, left_gpu, left_memory,
            left_storage, left_chassis, left_power, left_footer)


class PanelLeft(PanelWindow):
    WIDTH = 420
    ANCHOR = "LEFT"
    WANT = {'cpu', 'pressure', 'gpus', 'mem', 'diskio', 'disks',
            'numa', 'gpu_clients', 'sysinfo', 'net',
            'gpu_metrics', 'ecc', 'bmc', 'smart', 'dimms'}
    # Both panels are READERS. Recording moved out to the manimon-metrics user
    # service on 18 Aug 2026: a panel dies with the graphical session, and the
    # runs worth measuring are the overnight ones that outlive it. Set this back
    # to True only if the service is deliberately removed — two writers are
    # harmless (WAL, INSERT OR REPLACE on the same second) but pointless.
    RECORD = False

    def build(self):
        self.title_bar("◈  maniMon  ·  MACHINE")
        for section in SECTIONS:
            section.build(self)

    def refresh(self, s):
        for section in SECTIONS:
            section.refresh(self, s)

    # ── Statistics helpers ───────────────────────────────────────────────────
    # The metric store is queried at most once per column per STAT_EVERY, not
    # every 2 s repaint: a live reading changes constantly, but a 24 h p95 does
    # not, and running SQL on the UI path 30 times a second to prove that would
    # be absurd.
    STAT_EVERY = 30.0

    def _stat(self, column, hours=24):
        """Cached {'min','mean','p95','max','n'} or None."""
        if not getattr(self, 'stats', None) or not self.stats.available:
            return None
        cache = getattr(self, '_statcache', None)
        if cache is None:
            cache = self._statcache = {}
        key = (column, hours)
        hit = cache.get(key)
        now = time.monotonic()
        if hit and now - hit[1] < self.STAT_EVERY:
            return hit[0]
        try:
            val = self.stats.stats(column, hours)
        except Exception:
            val = None
        cache[key] = (val, now)
        return val

    def _stat_suffix(self, column, unit='', hours=24):
        """'  24h p95 78 max 91' — empty while the store is still filling."""
        st = self._stat(column, hours)
        # Under ~30 samples a p95 is nearly the maximum and says nothing.
        if not st or st['n'] < 30:
            return ''
        return (f'<span font="{FXS}" foreground="{DIM}">  24h p95 </span>'
                f'<span font="{FXS}" foreground="{W.INK}">{st["p95"]:.0f}{unit}</span>'
                f'<span font="{FXS}" foreground="{DIM}"> max </span>'
                f'<span font="{FXS}" foreground="{W.CAT[1]}">{st["max"]:.0f}{unit}</span>')

    def _frac_above(self, column, threshold, hours=24):
        if not getattr(self, 'stats', None) or not self.stats.available:
            return None
        cache = getattr(self, '_fracache', None)
        if cache is None:
            cache = self._fracache = {}
        key = (column, threshold, hours)
        hit = cache.get(key)
        now = time.monotonic()
        if hit and now - hit[1] < self.STAT_EVERY:
            return hit[0]
        try:
            val = self.stats.fraction_above(column, threshold, hours)
        except Exception:
            val = None
        cache[key] = (val, now)
        return val

    def _energy(self, hours=24):
        cache = getattr(self, '_encache', None)
        now = time.monotonic()
        if cache and now - cache[1] < self.STAT_EVERY:
            return cache[0]
        if not getattr(self, 'stats', None) or not self.stats.available:
            return None
        try:
            val = self.stats.energy_wh(hours)
        except Exception:
            val = None
        self._encache = (val, now)
        return val


if __name__ == "__main__":
    win = PanelLeft()
    win.connect("destroy", Gtk.main_quit)
    Gtk.main()
