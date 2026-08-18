"""
GPUs: VRAM, temperatures, board power and clocks.

Both halves of one section live here: the widgets it creates and the code that
fills them. They used to sit 150 lines apart in a single 850-line module.
"""

from .. import widgets as W
from ..window import *   # noqa: F401,F403
from ..markup import kv
from ...util import fmt_bytes, fmt_rate, fmt_elapsed

MAX_GPUS = 2

def build(p):
        # 3. GPU ---------------------------------------------------------------
        p.head("◉", "GPU  ·  ROCm", LIME)
        for i in range(MAX_GPUS):
            g = p.vbox()
            p._wid[f"gpubox{i}"] = g
            p.lbl(f"g{i}_name", c=g)
            p.lbl(f"g{i}_vram", c=g)
            p.wid(f"g{i}_vrambar", W.StackBar(height=9), c=g)
            p.lbl(f"g{i}_temp", c=g)
            p.lbl(f"g{i}_vr", c=g)          # VR temps + throttle (gpu_metrics)
            p.lbl(f"g{i}_pwr", c=g)
            p.wid(f"g{i}_pwrbar", W.Gauge(height=6), c=g)
            p.lbl(f"g{i}_clk", c=g)
            p.wid(f"g{i}_spark", W.Spark(height=18), mt=1, c=g)
        p.lbl("gpu_none")


def refresh(p, s):
    gpus = s.get('gpus') or []
    clients = s.get('gpu_clients') or {}
    p.vis("gpu_none", not gpus)
    if not gpus:
        p.L("gpu_none",
               f'<span font="{FS}" foreground="{DIM}">  no compute GPU detected</span>')
    for i in range(MAX_GPUS):
        box = p._wid.get(f"gpubox{i}")
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
        p.L(f"g{i}_name",
               f'<span font="{FB}" foreground="{LIME}">{g["name"][:26]}</span>'
               f'<span font="{FXS}" foreground="{DIM}">  {g["card"]}</span>'
               f'<span font="{FB}" foreground="{W.INK}">   {busy:3d}%</span>')
        p.L(f"g{i}_vram",
               kv("VRAM", f'{g["vram_used_gb"]:.1f} / {g["vram_total_gb"]:.0f} GB') +
               kv("   memctl", f'{g["mem_busy"]}%'))
        vb = p._wid.get(f"g{i}_vrambar")
        if vb:
            vb.set_segments([(g['vram_pct'] / 100, W.CAT[0])])
        p.L(f"g{i}_temp",
               kv("edge", f'{g["temp_edge"]:.0f}°', W.temp_color(g['temp_edge'])) +
               kv("  junc", f'{g["temp_junction"]:.0f}°',
                   W.temp_color(g['temp_junction'], 90, 105)) +
               kv("  mem", f'{g["temp_mem"]:.0f}°', W.temp_color(g['temp_mem'], 90, 100)) +
               kv("  fan", f'{g["fan_rpm"]}') +
               p._stat_suffix('gpu_temp_junction', '°'))

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
            p.L(f"g{i}_vr",
                   kv("VR g/s/m", f'{vr_txt}°',
                       W.temp_color(hottest_vr, 85, 100)) + thr)
            p.vis(f"g{i}_vr", True)
        else:
            p.vis(f"g{i}_vr", False)
        cap = g['power_cap'] or 1
        p.L(f"g{i}_pwr",
               kv("power", f'{g["power"]:.0f} / {cap:.0f} W') +
               kv("   ~", f'{g["tflops"]:.1f} TF  {g["bw_gbs"]:.0f} GB/s'))
        pb = p._wid.get(f"g{i}_pwrbar")
        if pb:
            pb.set(g['power'] / cap * 100, W.CAT[1])
        lw, lwm = g['link_width'], g['link_width_max']
        degraded = (lw != lwm) or (g['link_speed'] != g['link_speed_max'])
        p.L(f"g{i}_clk",
               kv("clk", f'{g["sclk"]}/{g["mclk"]}') +
               kv("  PCIe", f'x{lw}', W.WARN if degraded else WHITE) +
               f'<span font="{FXS}" foreground="{DIM}">  by </span>'
               f'<span font="{FXS}" foreground="{TEAL}">{who or "—"}</span>')
        sp = p._wid.get(f"g{i}_spark")
        if sp:
            sp.set_max(100)
            sp.set_col(W.CAT[0])
            sp.push(busy)
