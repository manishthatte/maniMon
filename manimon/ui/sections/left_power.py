"""
Thermal and power, and the electricity budget.

Both halves of one section live here: the widgets it creates and the code that
fills them. They used to sit 150 lines apart in a single 850-line module.
"""

from .. import widgets as W
from ..window import *   # noqa: F401,F403
from ...util import fmt_bytes, fmt_rate, fmt_elapsed

# Density level at which the two history sparklines and the temperature legend
# go. The current readings — which are what this section is for — stay; the
# trends are recoverable from `manimon report`, and the panel keeps its numbers
# rather than its decoration. See PanelWindow._fit.
D_NO_SPARKS = 5


def build(p):
        # 7. Thermal & power --------------------------------------------------
        p.head("⚡", "THERMAL  &  POWER", GOLD)
        p.lbl("th_row1")
        p.lbl("th_row2")
        p.wid("temp_spark", W.MultiSpark(series=2, height=30), mt=2)
        _tl = Gtk.DrawingArea(); _tl.set_size_request(-1, 13)
        _tl.connect("draw", lambda w, cr: draw_temp_legend(p, w, cr))
        p.wid("temp_legend", _tl)
        p.lbl("pwr_hdr", mt=2)
        p.wid("pwr_spark", W.Spark(height=20))
        p.lbl("pwr_stats")      # kWh + time-above-threshold, from metrics.py


def refresh(p, s):
    lean = getattr(p, 'density', 0) >= D_NO_SPARKS
    for k in ("temp_spark", "temp_legend", "pwr_spark"):
        p.vis(k, not lean)

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

    p.L("th_row1", render(parts[:4]))
    ccd = c.get('temp_ccd') or []
    if ccd:
        p.L("th_row2",
               f'<span font="{FXS}" foreground="{DIM}">chiplets  </span>' +
               '  '.join(f'<span font="{FXS}" foreground="{W.temp_color(v,75,90)}">'
                         f'{v:.0f}°</span>' for v in ccd) +
               (('<span font="{}" foreground="{}">   {}</span>'.format(
                   FXS, DIM, render(parts[4:8]))) if len(parts) > 4 else ''))
    else:
        p.L("th_row2", render(parts[4:8]))
    p.vis("th_row2", bool(ccd) or len(parts) > 4)

    ts = p._wid.get("temp_spark")
    if ts:
        gput = gpus[0]['temp_junction'] if gpus else 0.0
        ts.set_cols([W.CAT[0], W.CAT[1]])
        ts.push([cput, gput])

    gw = sum(g['power'] for g in gpus)
    cap = sum(g['power_cap'] for g in gpus) or 1
    cw = c.get('power')          # None while RAPL energy_uj is root-only
    if cw is None:
        p.L("pwr_hdr",
               f'<span font="{FB}" foreground="{W.INK}">{gw:.0f} W</span>'
               f'<span font="{FXS}" foreground="{DIM}">  GPU board / {cap:.0f}W cap'
               f'  ·  CPU pkg n/a</span>')
        sp = p._wid.get("pwr_spark")
        if sp:
            sp.set_max(cap)
            sp.set_col(W.CAT[1])
            sp.push(gw)
    else:
        total = cw + gw
        p.L("pwr_hdr",
               f'<span font="{FB}" foreground="{W.INK}">{total:.0f} W</span>'
               f'<span font="{FXS}" foreground="{DIM}">  total  ·  </span>'
               f'<span font="{FXS}" foreground="{W.CAT[2]}">CPU {cw:.0f}</span>'
               f'<span font="{FXS}" foreground="{DIM}"> + </span>'
               f'<span font="{FXS}" foreground="{W.CAT[1]}">GPU {gw:.0f}</span>'
               f'<span font="{FXS}" foreground="{DIM}"> W</span>')
        sp = p._wid.get("pwr_spark")
        if sp:
            sp.set_max(cap + 280)     # EPYC 9334 is a 210 W part
            sp.set_col(W.CAT[1])
            sp.push(total)

    budget(p)


def budget(p):
    """
    The statistics line: energy actually consumed, and compliance with the
    standing GPU rule (junction <= 85 C). Watts are a live reading; kWh and
    "how long was it too hot" are only answerable because the history is
    now stored.
    """
    e = p._energy(24)
    bits = []
    if e and e.get('total'):
        bits.append(f'<span font="{FXS}" foreground="{DIM}">24h </span>'
                    f'<span font="{FXS}" foreground="{W.INK}">'
                    f'{e["total"]/1000:.2f} kWh</span>')
        if e.get('cpu') and e.get('gpu'):
            bits.append(f'<span font="{FXS}" foreground="{DIM}">  '
                        f'(cpu {e["cpu"]/1000:.2f} + gpu {e["gpu"]/1000:.2f})</span>')
    frac = p._frac_above('gpu_temp_junction', 85, 24)
    if frac is not None:
        col = W.CRIT if frac > 0.10 else (W.WARN if frac > 0.02 else W.OK)
        bits.append(f'<span font="{FXS}" foreground="{DIM}">   &gt;85° </span>'
                    f'<span font="{FXS}" foreground="{col}">{frac*100:.1f}%</span>')
    if bits:
        p.L("pwr_stats", ''.join(bits))
        p.vis("pwr_stats", True)
    else:
        # Nothing to say until the store has history. Better an absent row
        # than one reading "0.00 kWh" because recording started a minute ago.
        p.vis("pwr_stats", False)


def draw_temp_legend(p, widget, cr):
    W.legend(cr, 0, widget.get_allocation().height / 2 - 1, [
        (W.CAT[0], "CPU Tctl"), (W.CAT[1], "GPU junction"),
    ])
    return True
