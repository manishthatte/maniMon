"""
Memory: usage breakdown, swap, channels and ECC.

Both halves of one section live here: the widgets it creates and the code that
fills them. They used to sit 150 lines apart in a single 850-line module.
"""

from .. import widgets as W
from ..window import *   # noqa: F401,F403
from ..markup import kv
from ...util import fmt_bytes, fmt_rate, fmt_elapsed

def build(p):
        # 4. Memory ------------------------------------------------------------
        p.head("▦", "MEMORY  ·  DDR5", BLUE)
        p.lbl("mem_hdr")
        p.wid("mem_bar", W.StackBar(height=13))
        _ml = Gtk.DrawingArea(); _ml.set_size_request(-1, 12)
        _ml.connect("draw", lambda w, cr: draw_mem_legend(p, w, cr))
        p.wid("mem_legend", _ml)
        p.lbl("mem_swap")
        p.wid("swap_bar", W.Gauge(height=6))
        p.lbl("mem_psi")
        p.lbl("mem_chan")       # populated channels vs bandwidth ceiling
        p.lbl("mem_ecc")        # ECC correctable / uncorrectable
        p.lbl("mem_hp")


def refresh(p, s):
    m = s.get('mem') or {}
    if not m:
        return
    tot = m['total_gb']
    p.L("mem_hdr",
           f'<span font="{FB}" foreground="{W.INK}">{m["used_gb"]:.1f}</span>'
           f'<span font="{FS}" foreground="{DIM}"> / {tot:.0f} GB</span>'
           f'<span font="{FS}" foreground="{W.status_color(m["used_pct"],80,92)}">'
           f'   {m["used_pct"]:.0f}%</span>'
           f'<span font="{FXS}" foreground="{DIM}">   avail '
           f'{m["free_gb"]+m["cache_gb"]+m["buf_gb"]:.0f} GB</span>')
    bar = p._wid.get("mem_bar")
    if bar:
        bar.set_segments([
            (m['used_gb'] / tot, W.CAT[0]),
            (m['buf_gb'] / tot, W.CAT[2]),
            (m['cache_gb'] / tot, W.CAT[1]),
        ])
    sw_col = W.CRIT if m['swap_used_gb'] > 1 else DIM
    p.L("mem_swap",
           kv("swap", f'{m["swap_used_gb"]:.1f} / {m["swap_total_gb"]:.0f} GB',
               sw_col) +
           kv("   dirty", f'{m["dirty_mb"]:.0f} MB'))
    sb = p._wid.get("swap_bar")
    if sb:
        sb.set(m['swap_pct'], W.CRIT if m['swap_pct'] > 1 else W.CAT[2])
    mem_psi = (s.get('pressure') or {}).get('memory', {})
    p.L("mem_psi",
           kv("PSI some", f'{mem_psi.get("some_avg10",0):.1f}%') +
           kv("   majflt/s", f'{m["pgmajfault_s"]:.0f}',
               W.WARN if m['pgmajfault_s'] > 5 else WHITE) +
           kv("   swapio", f'{m["swapin_s"]+m["swapout_s"]:.0f}/s',
               W.CRIT if (m['swapin_s'] + m['swapout_s']) > 10 else WHITE))
    # ── Memory channels ──────────────────────────────────────────────────
    # Empty slots cost bandwidth, and every DFT, GW and NEGF run is
    # bandwidth-bound. This row exists so the gap is visible, and so it can be
    # seen closing when the RDIMMs arrive.
    #
    # The ceiling shown is the BOARD's — what filling its slots would give —
    # because that is the one a purchase can reach. Showing the processor's
    # instead claimed 461 GB/s on an eight-slot board, a figure no amount of
    # memory could have achieved. The processor's appears after it, and only
    # when [memory].cpu_channels says what it is.
    dm = s.get('dimms') or {}
    if dm.get('present'):
        empty = dm.get('empty', 0)
        col = W.WARN if empty else W.OK
        gbs, board_max = dm.get('gbs'), dm.get('gbs_board_max')
        cpu_max = dm.get('gbs_cpu_max')
        # 420 px of panel does not fit both ceilings, so show the one that is
        # actionable now. While slots are empty the board's is what a purchase
        # buys. Once the board is full, the only remaining gap is the
        # processor's channel count — which needs a different board, and is
        # worth saying exactly then and not before.
        board_full = not empty
        cpu_bound = bool(board_full and cpu_max and board_max
                         and cpu_max > board_max)
        ceiling = cpu_max if cpu_bound else board_max
        frac = f'  {gbs:.0f}/{ceiling:.0f} GB/s' if gbs and ceiling else ''
        if cpu_bound:
            frac += ' cpu-limited'
        p.L("mem_chan",
               kv("channels", f'{dm["populated"]}/{dm["total_slots"]} slots',
                   col) +
               # No "N empty" suffix: 4/8 already says four are free, and the
               # slot count is drawn in the warning colour when any are. The
               # duplicate was the one token that pushed this row past 420 px
               # and got itself ellipsised.
               f'<span font="{FXS}" foreground="{DIM}">{frac}</span>')
        p.vis("mem_chan", True)
    else:
        p.vis("mem_chan", False)

    # ── ECC ──────────────────────────────────────────────────────────────
    ec = s.get('ecc') or {}
    if ec.get('present'):
        ce, ue = ec.get('ce', 0), ec.get('ue', 0)
        col = W.CRIT if ue else (W.WARN if ce else W.OK)
        p.L("mem_ecc",
               kv("ECC", f'{ce} correctable · {ue} uncorrectable', col))
        p.vis("mem_ecc", True)
    else:
        p.vis("mem_ecc", False)

    if m['hp_total_gb'] > 0.1:
        p.L("mem_hp",
               kv("hugepages", f'{m["hp_used_gb"]:.0f} / {m["hp_total_gb"]:.0f} GB'
                                f'  reserved', TEAL))
        p.vis("mem_hp", True)
    else:
        # nr_hugepages is 0 permanently and deliberately here (measured
        # 14 Aug: the 64 GB pool sat untouched). A row that always reads
        # zero teaches nothing.
        p.vis("mem_hp", False)


def draw_mem_legend(p, widget, cr):
    W.legend(cr, 0, widget.get_allocation().height / 2 - 2, [
        (W.CAT[0], "used"), (W.CAT[2], "buffers"),
        (W.CAT[1], "cache"), (None, "free"),
    ])
    return True
