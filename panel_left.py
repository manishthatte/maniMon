#!/usr/bin/env python3
"""
maniMon — LEFT PANEL: the machine.

Every hardware subsystem, at a glance, top to bottom in glance-frequency order:

  Clock + PSI pressure · CPU (64-thread heat map) · GPU · Memory ·
  Storage (every disk and partition, + I/O trend) · Thermal & power

NETWORK lives on the RIGHT panel: at 1440 px tall this one could not hold it
and still be readable, and the right panel had the room.

Colour policy lives in widgets.py: magnitude uses the sequential amber ramp,
composition uses the validated categorical trio, state uses the reserved
status palette — always alongside a number or glyph, never colour alone.

Author: Manish Jagdish Thatte
"""

import os, sys
os.environ['GDK_BACKEND'] = 'x11'
os.environ.setdefault('DISPLAY', ':0')

import time
_dir = os.path.dirname(os.path.abspath(__file__))
if _dir not in sys.path:
    sys.path.insert(0, _dir)

from panel_common import *              # noqa: F401,F403
import widgets as W
from collectors import fmt_bytes, fmt_rate, fmt_elapsed

MAX_MOUNTS = 15
MAX_DEVICES = 5
MAX_GPUS = 2


def _b(txt, col=WHITE, font=None):
    return f'<span font="{font or FS}" foreground="{col}">{txt}</span>'


def _kv(k, v, vcol=WHITE, kf=None, vf=None):
    return (f'<span font="{kf or FXS}" foreground="{DIM}">{k}</span>'
            f'<span font="{vf or FS}" foreground="{vcol}">  {v}</span>')


# Mounts that always earn their own row; the rest collapse into a summary line
ALWAYS_SHOW = {'/', '/home', '/tmp', '/var'}


def _notable(part, dev):
    """A partition worth a row of its own — everything else is noise at a glance."""
    return (dev['usb'] or bool(part['label'])
            or part['mount'] in ALWAYS_SHOW
            or part['pct'] >= 60
            or part['used'] >= 50 * 1024**3
            or (part['fstype'] == 'swap' and part['pct'] > 1))


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

    # ── Build ────────────────────────────────────────────────────────────────
    def build(self):
        self.title_bar("◈  MACHINE")

        # 1. Clock + pressure ------------------------------------------------
        self.lbl("clock", mt=2)
        self.lbl("date")
        prow = self.hbox(4)
        prow.set_margin_top(3)
        prow.set_margin_bottom(1)
        for nm in ('cpu', 'io', 'mem'):
            col = self.vbox(prow, expand=True)
            self.lbl(f"psi_{nm}_l", c=col)
            self.wid(f"psi_{nm}", W.Gauge(height=5), c=col)

        # 2. CPU --------------------------------------------------------------
        self.head("◈", "CPU  ·  EPYC 9334  32C / 64T", ORANGE)
        crow = self.hbox(10)
        order = []
        cores = sorted(range(32))
        for c in cores:
            order += [c, c + 32]           # SMT siblings adjacent
        self.grid = W.HeatGrid(8, 8, 21, order)
        crow.pack_start(self.grid, False, False, 0)
        stats = self.vbox(crow, spacing=0, expand=True)
        for k in ("cpu_tot", "cpu_split", "cpu_temp", "cpu_freq",
                  "cpu_load", "cpu_sched", "cpu_numa"):
            self.lbl(k, c=stats)
        self.wid("cpu_spark", W.Spark(height=20), mt=2, c=stats)
        self.wid("cpu_legend", W.HeatLegend(), mt=2)
        self.lbl("cpu_bins", mt=1)

        # 3. GPU ---------------------------------------------------------------
        self.head("◉", "GPU  ·  ROCm", LIME)
        for i in range(MAX_GPUS):
            g = self.vbox()
            self._wid[f"gpubox{i}"] = g
            self.lbl(f"g{i}_name", c=g)
            self.lbl(f"g{i}_vram", c=g)
            self.wid(f"g{i}_vrambar", W.StackBar(height=9), c=g)
            self.lbl(f"g{i}_temp", c=g)
            self.lbl(f"g{i}_vr", c=g)          # VR temps + throttle (gpu_metrics)
            self.lbl(f"g{i}_pwr", c=g)
            self.wid(f"g{i}_pwrbar", W.Gauge(height=6), c=g)
            self.lbl(f"g{i}_clk", c=g)
            self.wid(f"g{i}_spark", W.Spark(height=18), mt=1, c=g)
        self.lbl("gpu_none")

        # 4. Memory ------------------------------------------------------------
        self.head("▦", "MEMORY  ·  DDR5", BLUE)
        self.lbl("mem_hdr")
        self.wid("mem_bar", W.StackBar(height=13))
        _ml = Gtk.DrawingArea(); _ml.set_size_request(-1, 12)
        _ml.connect("draw", self._draw_mem_legend)
        self.wid("mem_legend", _ml)
        self.lbl("mem_swap")
        self.wid("swap_bar", W.Gauge(height=6))
        self.lbl("mem_psi")
        self.lbl("mem_chan")       # populated channels vs bandwidth ceiling
        self.lbl("mem_ecc")        # ECC correctable / uncorrectable
        self.lbl("mem_hp")

        # 5. Storage -----------------------------------------------------------
        self.head("◫", "STORAGE  ·  DISKS  &  PARTITIONS", CYAN)
        for d in range(MAX_DEVICES):
            self.lbl(f"dev{d}")
            self.lbl(f"devio{d}")
            self.lbl(f"devsmart{d}")       # wear / written / defects (SMART)
            self.lbl(f"devq{d}")
        for m in range(MAX_MOUNTS):
            self.wid(f"mnt{m}", W.RowBar(label_w=152, value_w=110))
        self.lbl("io_hdr", mt=3)
        self.wid("io_spark", W.DualSpark(height=26))

        # 6. Chassis / BMC -----------------------------------------------------
        # Hidden entirely until sensord publishes, so a machine without the
        # sampler deployed shows no empty scaffolding.
        self._wid["chassis_head"] = self.head("❉", "CHASSIS  ·  BMC", TEAL)
        self.lbl("bmc_fans")
        self.lbl("bmc_temps")
        self.lbl("bmc_power")
        self.lbl("bmc_absent")

        # 7. Thermal & power --------------------------------------------------
        self.head("⚡", "THERMAL  &  POWER", GOLD)
        self.lbl("th_row1")
        self.lbl("th_row2")
        self.wid("temp_spark", W.MultiSpark(series=2, height=30), mt=2)
        _tl = Gtk.DrawingArea(); _tl.set_size_request(-1, 13)
        _tl.connect("draw", self._draw_temp_legend)
        self.wid("temp_legend", _tl)
        self.lbl("pwr_hdr", mt=2)
        self.wid("pwr_spark", W.Spark(height=20))
        self.lbl("pwr_stats")      # kWh + time-above-threshold, from metrics.py

        # 8. Footer ------------------------------------------------------------
        self.box.pack_start(Gtk.Separator(), False, False, 0)
        self.lbl("foot1", mt=3)
        self.lbl("foot2")

    def _draw_temp_legend(self, widget, cr):
        W.legend(cr, 0, widget.get_allocation().height / 2 - 1, [
            (W.CAT[0], "CPU Tctl"), (W.CAT[1], "GPU junction"),
        ])
        return True

    def _draw_mem_legend(self, widget, cr):
        W.legend(cr, 0, widget.get_allocation().height / 2 - 2, [
            (W.CAT[0], "used"), (W.CAT[2], "buffers"),
            (W.CAT[1], "cache"), (None, "free"),
        ])
        return True

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

    # ── Refresh ──────────────────────────────────────────────────────────────
    def refresh(self, s):
        self._clock()
        self._cpu(s)
        self._gpu(s)
        self._mem(s)
        self._storage(s)
        self._chassis(s)
        self._power(s)
        self._footer(s)

    # 1 ------------------------------------------------------------------------
    def _clock(self):
        self.L("clock", f'<span font="{FCLK}" foreground="{GOLD}">'
                        f'{time.strftime("%H:%M:%S")}</span>')
        self.L("date", f'<span font="{FDAT}" foreground="{DIM}">'
                       f'  {time.strftime("%A,  %d %B %Y")}</span>')

    def _psi(self, s):
        p = s.get('pressure', {})
        for nm, key in (('cpu', 'cpu'), ('io', 'io'), ('mem', 'memory')):
            v = p.get(key, {}).get('some_avg10', 0.0)
            col = W.CRIT if v >= 40 else (W.WARN if v >= 10 else W.CAT[0])
            self.L(f"psi_{nm}_l",
                   f'<span font="{FXS}" foreground="{DIM}">{nm.upper()} </span>'
                   f'<span font="{FXS}" foreground="{col}">{v:.0f}%</span>')
            g = self._wid.get(f"psi_{nm}")
            if g:
                g.set(min(v, 100), col)

    # 2 ------------------------------------------------------------------------
    def _cpu(self, s):
        self._psi(s)
        c = s.get('cpu') or {}
        if not c:
            return
        per = c.get('per_cpu', [])
        self.grid.set_values(per)
        agg = c.get('agg', {})
        tot = agg.get('total', 0.0)
        self.L("cpu_tot",
               f'<span font="{FB}" foreground="{W.INK}">{tot:5.1f}%</span>'
               f'<span font="{FXS}" foreground="{DIM}">  busy</span>')
        iow = agg.get('iowait', 0)
        self.L("cpu_split",
               _kv("usr", f"{agg.get('user',0):.0f}") +
               _kv("  sys", f"{agg.get('sys',0):.0f}") +
               _kv("  io", f"{iow:.0f}", W.WARN if iow > 5 else WHITE))
        t = c.get('temp', 0)
        ccd = c.get('temp_ccd') or []
        self.L("cpu_temp",
               _kv("temp", f"{t:.1f}°C", W.temp_color(t, 75, 90)) +
               (f'<span font="{FXS}" foreground="{DIM}">  ccd {max(ccd):.0f}</span>'
                if ccd else '') +
               self._stat_suffix('cpu_temp', '°'))
        self.L("cpu_freq", _kv("freq", f"{c.get('freq_avg',0):.0f} MHz"))
        la = c.get('loadavg', [0, 0, 0])
        lcol = W.CRIT if la[0] > c.get('ncpu', 64) else (
            W.WARN if la[0] > c.get('ncore', 32) else WHITE)
        self.L("cpu_load", _kv("load", f"{la[0]:.1f} {la[1]:.1f} {la[2]:.1f}", lcol))
        self.L("cpu_sched",
               _kv("rq", f"{c.get('runq',0)}") +
               _kv("  blk", f"{c.get('blocked',0)}",
                   W.WARN if c.get('blocked', 0) else WHITE) +
               _kv(" cs", f"{c.get('ctxt_s',0)/1000:.0f}k", WHITE, FXS, FXS))

        numa = s.get('numa') or []
        if len(numa) > 1:
            self.L("cpu_numa", ' '.join(
                f'<span font="{FXS}" foreground="{DIM}">N{n["id"]}</span>'
                f'<span font="{FXS}" foreground="{WHITE}"> {n["cpu_pct"]:.0f}%</span>'
                for n in numa[:4]))
            self.vis("cpu_numa", True)
        else:
            # Single NUMA node on this box — the row would say nothing.
            self.vis("cpu_numa", False)

        sp = self._wid.get("cpu_spark")
        if sp:
            sp.set_max(100)
            sp.set_col(W.CAT[0])
            sp.push(tot)

        b = c.get('freq_bins', {})
        # The bin edges follow the part's real ceiling, so the label must too —
        # a hardcoded "≥3.2G" would be wrong the moment the governor changes.
        ceil = c.get('freq_ceiling', 0) or 0
        self.L("cpu_bins",
               _kv(f"boost≥{ceil*0.82/1000:.1f}G", f"{b.get('boost',0)}", W.CAT[1]) +
               _kv("   mid", f"{b.get('mid',0)}", W.CAT[2]) +
               _kv("   low", f"{b.get('low',0)}", W.CAT[0]) +
               f'<span font="{FXS}" foreground="{DIM}">   {c.get("freq_driver","")}'
               f'/{c.get("governor","")}</span>')

    # 3 ------------------------------------------------------------------------
    def _gpu(self, s):
        gpus = s.get('gpus') or []
        clients = s.get('gpu_clients') or {}
        self.vis("gpu_none", not gpus)
        if not gpus:
            self.L("gpu_none",
                   f'<span font="{FS}" foreground="{DIM}">  no compute GPU detected</span>')
        for i in range(MAX_GPUS):
            box = self._wid.get(f"gpubox{i}")
            if i >= len(gpus):
                if box:
                    box.set_visible(False)
                    box.set_no_show_all(True)
                continue
            if box:
                box.set_visible(True)
                box.set_no_show_all(False)
            g = gpus[i]
            busy = g['busy']
            who = ', '.join(sorted(set(clients.values())))[:18]
            self.L(f"g{i}_name",
                   f'<span font="{FB}" foreground="{LIME}">{g["name"][:26]}</span>'
                   f'<span font="{FXS}" foreground="{DIM}">  {g["card"]}</span>'
                   f'<span font="{FB}" foreground="{W.INK}">   {busy:3d}%</span>')
            self.L(f"g{i}_vram",
                   _kv("VRAM", f'{g["vram_used_gb"]:.1f} / {g["vram_total_gb"]:.0f} GB') +
                   _kv("   memctl", f'{g["mem_busy"]}%'))
            vb = self._wid.get(f"g{i}_vrambar")
            if vb:
                vb.set_segments([(g['vram_pct'] / 100, W.CAT[0])])
            self.L(f"g{i}_temp",
                   _kv("edge", f'{g["temp_edge"]:.0f}°', W.temp_color(g['temp_edge'])) +
                   _kv("  junc", f'{g["temp_junction"]:.0f}°',
                       W.temp_color(g['temp_junction'], 90, 105)) +
                   _kv("  mem", f'{g["temp_mem"]:.0f}°', W.temp_color(g['temp_mem'], 90, 100)) +
                   _kv("  fan", f'{g["fan_rpm"]}') +
                   self._stat_suffix('gpu_temp_junction', '°'))

            # From gpu_metrics: the three VR temperatures hwmon does not expose,
            # and the throttle state — which is the direct answer to "why is
            # this run slower than the last one" and had no display at all.
            gm = (s.get('gpu_metrics') or {}).get(g['card']) or {}
            if gm.get('supported'):
                vr = [gm.get('temp_vr_gfx'), gm.get('temp_vr_soc'), gm.get('temp_vr_mem')]
                vr_txt = '/'.join(f'{v:.0f}' if v is not None else '—' for v in vr)
                hottest_vr = max((v for v in vr if v is not None), default=0)
                if gm.get('throttled'):
                    why = ', '.join(gm.get('throttle_reasons') or [])[:22]
                    thr = (f'<span font="{FXS}" foreground="{W.CRIT}">  ▲ throttled'
                           f' {why}</span>')
                elif gm.get('throttle_bits_raw') and not gm.get('throttle_trusted'):
                    # The bits are set but the GPU is idle, where this card's
                    # firmware reports nonsense. Say nothing rather than warn.
                    thr = f'<span font="{FXS}" foreground="{DIM}">  not throttled</span>'
                else:
                    thr = f'<span font="{FXS}" foreground="{W.OK}">  not throttled</span>'
                self.L(f"g{i}_vr",
                       _kv("VR g/s/m", f'{vr_txt}°',
                           W.temp_color(hottest_vr, 85, 100)) + thr)
                self.vis(f"g{i}_vr", True)
            else:
                self.vis(f"g{i}_vr", False)
            cap = g['power_cap'] or 1
            self.L(f"g{i}_pwr",
                   _kv("power", f'{g["power"]:.0f} / {cap:.0f} W') +
                   _kv("   ~", f'{g["tflops"]:.1f} TF  {g["bw_gbs"]:.0f} GB/s'))
            pb = self._wid.get(f"g{i}_pwrbar")
            if pb:
                pb.set(g['power'] / cap * 100, W.CAT[1])
            lw, lwm = g['link_width'], g['link_width_max']
            degraded = (lw != lwm) or (g['link_speed'] != g['link_speed_max'])
            self.L(f"g{i}_clk",
                   _kv("clk", f'{g["sclk"]}/{g["mclk"]}') +
                   _kv("  PCIe", f'x{lw}', W.WARN if degraded else WHITE) +
                   f'<span font="{FXS}" foreground="{DIM}">  by </span>'
                   f'<span font="{FXS}" foreground="{TEAL}">{who or "—"}</span>')
            sp = self._wid.get(f"g{i}_spark")
            if sp:
                sp.set_max(100)
                sp.set_col(W.CAT[0])
                sp.push(busy)

    # 4 ------------------------------------------------------------------------
    def _mem(self, s):
        m = s.get('mem') or {}
        if not m:
            return
        tot = m['total_gb']
        self.L("mem_hdr",
               f'<span font="{FB}" foreground="{W.INK}">{m["used_gb"]:.1f}</span>'
               f'<span font="{FS}" foreground="{DIM}"> / {tot:.0f} GB</span>'
               f'<span font="{FS}" foreground="{W.status_color(m["used_pct"],80,92)}">'
               f'   {m["used_pct"]:.0f}%</span>'
               f'<span font="{FXS}" foreground="{DIM}">   avail '
               f'{m["free_gb"]+m["cache_gb"]+m["buf_gb"]:.0f} GB</span>')
        bar = self._wid.get("mem_bar")
        if bar:
            bar.set_segments([
                (m['used_gb'] / tot, W.CAT[0]),
                (m['buf_gb'] / tot, W.CAT[2]),
                (m['cache_gb'] / tot, W.CAT[1]),
            ])
        sw_col = W.CRIT if m['swap_used_gb'] > 1 else DIM
        self.L("mem_swap",
               _kv("swap", f'{m["swap_used_gb"]:.1f} / {m["swap_total_gb"]:.0f} GB',
                   sw_col) +
               _kv("   dirty", f'{m["dirty_mb"]:.0f} MB'))
        sb = self._wid.get("swap_bar")
        if sb:
            sb.set(m['swap_pct'], W.CRIT if m['swap_pct'] > 1 else W.CAT[2])
        p = (s.get('pressure') or {}).get('memory', {})
        self.L("mem_psi",
               _kv("PSI some", f'{p.get("some_avg10",0):.1f}%') +
               _kv("   majflt/s", f'{m["pgmajfault_s"]:.0f}',
                   W.WARN if m['pgmajfault_s'] > 5 else WHITE) +
               _kv("   swapio", f'{m["swapin_s"]+m["swapout_s"]:.0f}/s',
                   W.CRIT if (m['swapin_s'] + m['swapout_s']) > 10 else WHITE))
        # ── Memory channels ──────────────────────────────────────────────────
        # Half the slots are empty on a 12-channel part, so this machine reaches
        # roughly a third of the bandwidth the CPU can address — and every DFT,
        # GW and NEGF run here is bandwidth-bound. This row exists so the gap is
        # visible, and so it can be seen closing when the RDIMMs arrive.
        dm = s.get('dimms') or {}
        if dm.get('present'):
            empty = dm.get('empty', 0)
            col = W.WARN if empty else W.OK
            gbs, cpu_max = dm.get('gbs'), dm.get('gbs_cpu_max')
            frac = (f'  {gbs:.0f} of {cpu_max:.0f} GB/s'
                    if gbs and cpu_max else '')
            self.L("mem_chan",
                   _kv("channels", f'{dm["populated"]} of {dm["total_slots"]} slots',
                       col) +
                   f'<span font="{FXS}" foreground="{DIM}">{frac}</span>' +
                   (f'<span font="{FXS}" foreground="{W.WARN}">   {empty} empty</span>'
                    if empty else ''))
            self.vis("mem_chan", True)
        else:
            self.vis("mem_chan", False)

        # ── ECC ──────────────────────────────────────────────────────────────
        ec = s.get('ecc') or {}
        if ec.get('present'):
            ce, ue = ec.get('ce', 0), ec.get('ue', 0)
            col = W.CRIT if ue else (W.WARN if ce else W.OK)
            self.L("mem_ecc",
                   _kv("ECC", f'{ce} correctable · {ue} uncorrectable', col))
            self.vis("mem_ecc", True)
        else:
            self.vis("mem_ecc", False)

        if m['hp_total_gb'] > 0.1:
            self.L("mem_hp",
                   _kv("hugepages", f'{m["hp_used_gb"]:.0f} / {m["hp_total_gb"]:.0f} GB'
                                    f'  reserved', TEAL))
            self.vis("mem_hp", True)
        else:
            # nr_hugepages is 0 permanently and deliberately here (measured
            # 14 Aug: the 64 GB pool sat untouched). A row that always reads
            # zero teaches nothing.
            self.vis("mem_hp", False)

    # 6 ------------------------------------------------------------------------
    def _chassis(self, s):
        """
        Board-level sensors from the BMC: chassis fans, VRM/DIMM/inlet
        temperatures, voltage rails, PSU draw. None of this tier was read at
        all before 18 Aug 2026 — the BMC has always been there, the monitor
        simply never asked it.
        """
        b = s.get('bmc') or {}
        head = self._wid.get("chassis_head")

        if not b.get('present'):
            # Show one quiet line explaining how to turn it on, rather than an
            # empty section or a silent omission.
            if head:
                head.set_visible(True)
                head.set_no_show_all(False)
            self.L("bmc_absent",
                   f'<span font="{FXS}" foreground="{DIM}">  sampler not running · '
                   f'sudo systemctl start manimon-sensors</span>')
            for k in ("bmc_fans", "bmc_temps", "bmc_power"):
                self.vis(k, False)
            self.vis("bmc_absent", True)
            return

        self.vis("bmc_absent", False)
        if head:
            head.set_visible(True)
            head.set_no_show_all(False)

        stale = b.get('stale')
        age_txt = (f'  <span font="{FXS}" foreground="{W.WARN}">stale '
                   f'{b.get("age",0):.0f}s</span>' if stale else '')

        fans = b.get('fans') or []
        dead = b.get('dead_fans') or []
        if fans:
            fcol = W.CRIT if dead else W.OK
            spread = (f'{b["fan_min"]:.0f}–{b["fan_max"]:.0f}'
                      if b.get('fan_min') is not None else '—')
            self.L("bmc_fans",
                   _kv("fans", f'{b["fan_count"]} · {spread} rpm', fcol) +
                   (f'<span font="{FXS}" foreground="{W.CRIT}">   {len(dead)} DEAD: '
                    f'{",".join(dead)[:18]}</span>' if dead else '') + age_txt)
            self.vis("bmc_fans", True)
        else:
            self.vis("bmc_fans", False)

        temps = b.get('temps') or []
        if temps:
            tmax = b.get('temp_max') or 0
            self.L("bmc_temps",
                   _kv("board", f'{len(temps)} sensors · max {tmax:.0f}°',
                       W.temp_color(tmax, 60, 80)) +
                   f'<span font="{FXS}" foreground="{DIM}">  '
                   f'{(b.get("temp_hottest") or "")[:14]}</span>')
            self.vis("bmc_temps", True)
        else:
            self.vis("bmc_temps", False)

        bits = []
        if b.get('power'):
            bits.append(_kv("PSU", f'{b["power"]:.0f} W', W.CAT[1]))
        rails = b.get('rails_off_nominal') or []
        if rails:
            bits.append(f'<span font="{FXS}" foreground="{W.WARN}">   rails off: '
                        f'{",".join(rails)[:20]}</span>')
        elif b.get('volts'):
            bits.append(f'<span font="{FXS}" foreground="{W.OK}">   '
                        f'{len(b["volts"])} rails nominal</span>')
        if bits:
            self.L("bmc_power", ''.join(bits))
            self.vis("bmc_power", True)
        else:
            self.vis("bmc_power", False)

    # 5 ------------------------------------------------------------------------
    def _storage(self, s):
        devs = s.get('disks') or []
        io = s.get('diskio') or {}
        total_parts = sum(len(d['parts']) for d in devs[:MAX_DEVICES])
        di = mi = 0
        for dev in devs[:MAX_DEVICES]:
            if di >= MAX_DEVICES:
                break
            kind = 'USB' if dev['usb'] else ('HDD' if dev['rotational'] else 'SSD')
            icon = '⏏' if dev['usb'] else '◈'
            t = dev.get('temp')
            tstr = (f'  <span foreground="{W.temp_color(t,55,70)}">{t:.0f}°C</span>'
                    if t else '')
            self.L(f"dev{di}",
                   f'<span font="{FB}" foreground="{CYAN}">{icon} {dev["dev"]}</span>'
                   f'<span font="{FXS}" foreground="{DIM}">  {fmt_bytes(dev["size"])}'
                   f'  {kind}</span><span font="{FXS}">{tstr}</span>')
            d = io.get(dev['dev'])
            if d:
                qcol = W.CRIT if d['queue'] > 8 else (W.WARN if d['queue'] > 2 else DIM)
                self.L(f"devio{di}",
                       f'<span font="{FXS}" foreground="{DIM}">   r </span>'
                       f'<span font="{FXS}" foreground="{W.CAT[0]}">{fmt_rate(d["r_bps"])}</span>'
                       f'<span font="{FXS}" foreground="{DIM}">  w </span>'
                       f'<span font="{FXS}" foreground="{W.CAT[1]}">{fmt_rate(d["w_bps"])}</span>'
                       f'<span font="{FXS}" foreground="{DIM}">   q</span>'
                       f'<span font="{FXS}" foreground="{qcol}">{d["queue"]}</span>'
                       f'<span font="{FXS}" foreground="{DIM}">   await '
                       f'{d["r_await"]:.1f}/{d["w_await"]:.1f}ms</span>')
            else:
                self.L(f"devio{di}", f'<span font="{FXS}" foreground="{DIM}">   idle</span>')

            # SMART: wear, written volume and defect counters. The panel used
            # to show only capacity and temperature, which says nothing about
            # whether the drive is dying.
            sm = (s.get('smart') or {}).get(dev['dev'])
            if sm:
                bits = []
                life = sm.get('life_pct')
                if isinstance(life, (int, float)):
                    bits.append(_kv("life", f'{life:.0f}%',
                                    W.CRIT if life <= 10 else
                                    (W.WARN if life <= 25 else W.OK), FXS, FXS))
                if sm.get('bytes_written'):
                    # "~" when the unit was inferred rather than stated by the
                    # drive, so an approximate figure never reads as measured.
                    pre = '~' if sm.get('writes_inferred') else ''
                    bits.append(_kv("  written",
                                    pre + fmt_bytes(sm['bytes_written']),
                                    WHITE, FXS, FXS))
                elif sm.get('writes_raw') is not None:
                    # The drive counts writes but will not say in what unit, so
                    # show the counter rather than silently omitting the line —
                    # a missing figure looks like a drive that never writes.
                    bits.append(_kv("  written", f'{sm["writes_raw"]:,}?',
                                    W.INK_DIM, FXS, FXS))
                defects = ((sm.get('reallocated') or 0) + (sm.get('pending') or 0) +
                           (sm.get('media_errors') or 0))
                if defects:
                    bits.append(_kv("  defects", f'{defects}', W.CRIT, FXS, FXS))
                elif sm.get('healthy'):
                    bits.append(f'<span font="{FXS}" foreground="{W.OK}">  healthy</span>')
                if sm.get('crit_temp_time'):
                    bits.append(f'<span font="{FXS}" foreground="{W.WARN}">  '
                                f'{sm["crit_temp_time"]}m over temp</span>')
                self.L(f"devsmart{di}", ''.join(bits))
                self.vis(f"devsmart{di}", True)
            else:
                self.vis(f"devsmart{di}", False)

            self.vis(f"dev{di}", True)
            self.vis(f"devio{di}", True)
            di += 1

            if total_parts <= MAX_MOUNTS:
                shown, quiet = dev['parts'], []          # they all fit — show all
            else:
                shown = [p for p in dev['parts'] if _notable(p, dev)]
                quiet = [p for p in dev['parts'] if not _notable(p, dev)]
            for part in shown:
                if mi >= MAX_MOUNTS:
                    break
                rb = self._wid.get(f"mnt{mi}")
                if not rb:
                    break
                name = part['label'] or part['mount']
                if len(name) > 16:
                    name = '…' + name[-15:]
                rb.set(f"  {name}",
                       f'{fmt_bytes(part["used"])}/{fmt_bytes(part["total"])}',
                       part['pct'], W.status_color(part['pct']))
                self.vis(f"mnt{mi}", True)
                mi += 1

            # Eight near-empty system partitions were pushing everything else
            # off the panel. They are collapsed to one line and expand the
            # moment any of them starts to fill.
            if quiet:
                worst = max(quiet, key=lambda p: p['pct'])
                self.L(f"devq{di-1}",
                       f'<span font="{FXS}" foreground="{DIM}">   +{len(quiet)} quiet '
                       f'partitions  ·  largest {worst["mount"]} '
                       f'{worst["pct"]:.0f}%</span>')
                self.vis(f"devq{di-1}", True)
            else:
                self.vis(f"devq{di-1}", False)

        for i in range(di, MAX_DEVICES):
            self.vis(f"dev{i}", False)
            self.vis(f"devio{i}", False)
            self.vis(f"devsmart{i}", False)
            self.vis(f"devq{i}", False)
        for i in range(mi, MAX_MOUNTS):
            self.vis(f"mnt{i}", False)

        rd = sum(d['r_bps'] for d in io.values())
        wr = sum(d['w_bps'] for d in io.values())
        busiest = max(io.items(), key=lambda kv: kv[1]['r_bps'] + kv[1]['w_bps'],
                      default=(None, None))
        who = busiest[0] if busiest[0] and (rd + wr) > 65536 else 'idle'
        self.L("io_hdr",
               f'<span font="{FXS}" foreground="{DIM}">all disks  </span>'
               f'<span font="{FS}" foreground="{W.CAT[0]}">↓ {fmt_rate(rd)}</span>'
               f'<span font="{FS}" foreground="{W.CAT[1]}">   ↑ {fmt_rate(wr)}</span>'
               f'<span font="{FXS}" foreground="{DIM}">   {who}</span>')
        sp = self._wid.get("io_spark")
        if sp:
            sp.push(rd, wr)

    # 7 ------------------------------------------------------------------------
    def _power(self, s):
        c = s.get('cpu') or {}
        gpus = s.get('gpus') or []
        disks = s.get('disks') or []
        parts = []
        cput = c.get('temp', 0)
        if cput:
            parts.append(('CPU', cput, 75, 90))
        for g in gpus:
            parts.append(('GPU', g['temp_junction'], 90, 105))
        for d in disks:
            if d.get('temp'):
                parts.append((d['dev'][:6], d['temp'], 55, 70))
        for n in (s.get('net') or []):
            if n.get('temp'):
                parts.append(('NIC', n['temp'], 75, 90))
                break

        def render(chunk):
            return '  '.join(
                f'<span font="{FXS}" foreground="{DIM}">{nm} </span>'
                f'<span font="{FS}" foreground="{W.temp_color(v, w, cr)}">{v:.0f}°</span>'
                for nm, v, w, cr in chunk)

        self.L("th_row1", render(parts[:4]))
        ccd = c.get('temp_ccd') or []
        if ccd:
            self.L("th_row2",
                   f'<span font="{FXS}" foreground="{DIM}">chiplets  </span>' +
                   '  '.join(f'<span font="{FXS}" foreground="{W.temp_color(v,75,90)}">'
                             f'{v:.0f}°</span>' for v in ccd) +
                   (('<span font="{}" foreground="{}">   {}</span>'.format(
                       FXS, DIM, render(parts[4:8]))) if len(parts) > 4 else ''))
        else:
            self.L("th_row2", render(parts[4:8]))
        self.vis("th_row2", bool(ccd) or len(parts) > 4)

        ts = self._wid.get("temp_spark")
        if ts:
            gput = gpus[0]['temp_junction'] if gpus else 0.0
            ts.set_cols([W.CAT[0], W.CAT[1]])
            ts.push([cput, gput])

        gw = sum(g['power'] for g in gpus)
        cap = sum(g['power_cap'] for g in gpus) or 1
        cw = c.get('power')          # None while RAPL energy_uj is root-only
        if cw is None:
            self.L("pwr_hdr",
                   f'<span font="{FB}" foreground="{W.INK}">{gw:.0f} W</span>'
                   f'<span font="{FXS}" foreground="{DIM}">  GPU board / {cap:.0f}W cap'
                   f'  ·  CPU pkg n/a</span>')
            sp = self._wid.get("pwr_spark")
            if sp:
                sp.set_max(cap)
                sp.set_col(W.CAT[1])
                sp.push(gw)
        else:
            total = cw + gw
            self.L("pwr_hdr",
                   f'<span font="{FB}" foreground="{W.INK}">{total:.0f} W</span>'
                   f'<span font="{FXS}" foreground="{DIM}">  total  ·  </span>'
                   f'<span font="{FXS}" foreground="{W.CAT[2]}">CPU {cw:.0f}</span>'
                   f'<span font="{FXS}" foreground="{DIM}"> + </span>'
                   f'<span font="{FXS}" foreground="{W.CAT[1]}">GPU {gw:.0f}</span>'
                   f'<span font="{FXS}" foreground="{DIM}"> W</span>')
            sp = self._wid.get("pwr_spark")
            if sp:
                sp.set_max(cap + 280)     # EPYC 9334 is a 210 W part
                sp.set_col(W.CAT[1])
                sp.push(total)

        self._budget()

    def _budget(self):
        """
        The statistics line: energy actually consumed, and compliance with the
        standing GPU rule (junction <= 85 C). Watts are a live reading; kWh and
        "how long was it too hot" are only answerable because the history is
        now stored.
        """
        e = self._energy(24)
        bits = []
        if e and e.get('total'):
            bits.append(f'<span font="{FXS}" foreground="{DIM}">24h </span>'
                        f'<span font="{FXS}" foreground="{W.INK}">'
                        f'{e["total"]/1000:.2f} kWh</span>')
            if e.get('cpu') and e.get('gpu'):
                bits.append(f'<span font="{FXS}" foreground="{DIM}">  '
                            f'(cpu {e["cpu"]/1000:.2f} + gpu {e["gpu"]/1000:.2f})</span>')
        frac = self._frac_above('gpu_temp_junction', 85, 24)
        if frac is not None:
            col = W.CRIT if frac > 0.10 else (W.WARN if frac > 0.02 else W.OK)
            bits.append(f'<span font="{FXS}" foreground="{DIM}">   &gt;85° </span>'
                        f'<span font="{FXS}" foreground="{col}">{frac*100:.1f}%</span>')
        if bits:
            self.L("pwr_stats", ''.join(bits))
            self.vis("pwr_stats", True)
        else:
            # Nothing to say until the store has history. Better an absent row
            # than one reading "0.00 kWh" because recording started a minute ago.
            self.vis("pwr_stats", False)

    # 8 ------------------------------------------------------------------------
    def _footer(self, s):
        si = s.get('sysinfo') or {}
        # ROCm missing while /dev/kfd exists is a real, work-blocking state —
        # the GPU is a display adapter. Say so instead of printing "?".
        rocm = si.get('rocm', '?')
        rocm_col = DIM if si.get('rocm_ok') else W.WARN
        self.L("foot1",
               f'<span font="{FXS}" foreground="{DIM}">{si.get("host","?")}  ·  '
               f'{si.get("kernel","?")}  ·  ROCm </span>'
               f'<span font="{FXS}" foreground="{rocm_col}">{rocm}</span>')

        # Store state, so it is obvious whether statistics are accumulating.
        store = ''
        if getattr(self, 'stats', None) and self.stats.available:
            n = self._stat('cpu_pct', 24)
            if n:
                store = f'  ·  {n["n"]} samples/24h'
        else:
            store = '  ·  no metric store'
        self.L("foot2",
               f'<span font="{FXS}" foreground="{DIM}">up '
               f'{fmt_elapsed(si.get("uptime",0))}  ·  venv {si.get("venv","—")}'
               f'{store}  ·  {time.strftime("%H:%M:%S")}</span>')


if __name__ == "__main__":
    win = PanelLeft()
    win.connect("destroy", Gtk.main_quit)
    Gtk.main()
