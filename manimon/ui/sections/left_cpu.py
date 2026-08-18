"""
CPU: per-core heat grid, aggregate, temperature, NUMA.

Both halves of one section live here: the widgets it creates and the code that
fills them. They used to sit 150 lines apart in a single 850-line module.
"""

from .. import widgets as W
from ..window import *   # noqa: F401,F403
from ..markup import kv
from ...util import fmt_bytes, fmt_rate, fmt_elapsed

# Density level at which the heat map goes wide-and-short and its legend and
# boost-bin row drop. See PanelWindow._fit.
D_COMPACT_GRID = 4
# Last thing to go before any actual reading is dropped: the history sparkline.
# The trend is in `manimon report`; the number beside it is not anywhere else.
D_NO_SPARK = 6

def build(p):
        # 2. CPU --------------------------------------------------------------
        p.head("◈", "CPU  ·  EPYC 9334  32C / 64T", ORANGE)
        crow = p.hbox(10)
        order = []
        cores = sorted(range(32))
        for c in cores:
            order += [c, c + 32]           # SMT siblings adjacent
        p.grid = W.HeatGrid(8, 8, 21, order)
        crow.pack_start(p.grid, False, False, 0)
        stats = p.vbox(crow, spacing=0, expand=True)
        for k in ("cpu_tot", "cpu_split", "cpu_temp", "cpu_freq",
                  "cpu_load", "cpu_sched", "cpu_numa"):
            p.lbl(k, c=stats)
        p.wid("cpu_spark", W.Spark(height=20), mt=2, c=stats)
        p.wid("cpu_legend", W.HeatLegend(), mt=2)
        p.lbl("cpu_bins", mt=1)


def refresh(p, s):
    c = s.get('cpu') or {}
    if not c:
        return
    per = c.get('per_cpu', [])

    # Density 4: the heat map is the tallest single block on the panel. Laid
    # out wide instead of square it costs 90 px rather than 182 and loses
    # nothing — SMT siblings are still adjacent. The legend and the boost-bin
    # row go with it; both explain the grid rather than report anything.
    density = getattr(p, 'density', 0)
    compact = density >= D_COMPACT_GRID
    n = len(per) or 64
    cols = max(8, -(-n // 4)) if compact else 8
    p.grid.reshape(cols, -(-n // cols))
    p.vis("cpu_legend", not compact)
    p.vis("cpu_bins", not compact)
    p.vis("cpu_spark", density < D_NO_SPARK)

    p.grid.set_values(per)
    agg = c.get('agg', {})
    tot = agg.get('total', 0.0)
    p.L("cpu_tot",
           f'<span font="{FB}" foreground="{W.INK}">{tot:5.1f}%</span>'
           f'<span font="{FXS}" foreground="{DIM}">  busy</span>')
    iow = agg.get('iowait', 0)
    p.L("cpu_split",
           kv("usr", f"{agg.get('user',0):.0f}") +
           kv("  sys", f"{agg.get('sys',0):.0f}") +
           kv("  io", f"{iow:.0f}", W.WARN if iow > 5 else WHITE))
    t = c.get('temp', 0)
    ccd = c.get('temp_ccd') or []
    p.L("cpu_temp",
           kv("temp", f"{t:.1f}°C", W.temp_color(t, 75, 90)) +
           (f'<span font="{FXS}" foreground="{DIM}">  ccd {max(ccd):.0f}</span>'
            if ccd else '') +
           p._stat_suffix('cpu_temp', '°'))
    p.L("cpu_freq", kv("freq", f"{c.get('freq_avg',0):.0f} MHz"))
    la = c.get('loadavg', [0, 0, 0])
    lcol = W.CRIT if la[0] > c.get('ncpu', 64) else (
        W.WARN if la[0] > c.get('ncore', 32) else WHITE)
    p.L("cpu_load", kv("load", f"{la[0]:.1f} {la[1]:.1f} {la[2]:.1f}", lcol))
    p.L("cpu_sched",
           kv("rq", f"{c.get('runq',0)}") +
           kv("  blk", f"{c.get('blocked',0)}",
               W.WARN if c.get('blocked', 0) else WHITE) +
           kv(" cs", f"{c.get('ctxt_s',0)/1000:.0f}k", WHITE, FXS, FXS))

    numa = s.get('numa') or []
    if len(numa) > 1:
        p.L("cpu_numa", ' '.join(
            f'<span font="{FXS}" foreground="{DIM}">N{n["id"]}</span>'
            f'<span font="{FXS}" foreground="{WHITE}"> {n["cpu_pct"]:.0f}%</span>'
            for n in numa[:4]))
        p.vis("cpu_numa", True)
    else:
        # Single NUMA node on this box — the row would say nothing.
        p.vis("cpu_numa", False)

    sp = p._wid.get("cpu_spark")
    if sp:
        sp.set_max(100)
        sp.set_col(W.CAT[0])
        sp.push(tot)

    b = c.get('freq_bins', {})
    # The bin edges follow the part's real ceiling, so the label must too —
    # a hardcoded "≥3.2G" would be wrong the moment the governor changes.
    ceil = c.get('freq_ceiling', 0) or 0
    p.L("cpu_bins",
           kv(f"boost≥{ceil*0.82/1000:.1f}G", f"{b.get('boost',0)}", W.CAT[1]) +
           kv("   mid", f"{b.get('mid',0)}", W.CAT[2]) +
           kv("   low", f"{b.get('low',0)}", W.CAT[0]) +
           f'<span font="{FXS}" foreground="{DIM}">   {c.get("freq_driver","")}'
           f'/{c.get("governor","")}</span>')
