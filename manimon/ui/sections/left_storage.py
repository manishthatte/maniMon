"""
Filesystems, block devices, drive health and I/O.

Both halves of one section live here: the widgets it creates and the code that
fills them. They used to sit 150 lines apart in a single 850-line module.
"""

from .. import widgets as W
from ..window import *   # noqa: F401,F403
from ..markup import kv
from ...util import fmt_bytes, fmt_rate, fmt_elapsed

MAX_MOUNTS = 15
MAX_DEVICES = 5

# Mounts that always earn their own row; the rest collapse into a summary line
ALWAYS_SHOW = {'/', '/home', '/tmp', '/var'}


def _notable(part, dev):
    """A partition worth a row of its own — everything else is noise at a glance."""
    return (dev['usb'] or bool(part['label'])
            or part['mount'] in ALWAYS_SHOW
            or part['pct'] >= 60
            or part['used'] >= 50 * 1024**3
            or (part['fstype'] == 'swap' and part['pct'] > 1))

def build(p):
        # 5. Storage -----------------------------------------------------------
        p.head("◫", "STORAGE  ·  DISKS  &  PARTITIONS", CYAN)
        for d in range(MAX_DEVICES):
            p.lbl(f"dev{d}")
            p.lbl(f"devio{d}")
            p.lbl(f"devsmart{d}")       # wear / written / defects (SMART)
            p.lbl(f"devq{d}")
        for m in range(MAX_MOUNTS):
            p.wid(f"mnt{m}", W.RowBar(label_w=152, value_w=110))
        p.lbl("io_hdr", mt=3)
        p.wid("io_spark", W.DualSpark(height=26))


def refresh(p, s):
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
        p.L(f"dev{di}",
               f'<span font="{FB}" foreground="{CYAN}">{icon} {dev["dev"]}</span>'
               f'<span font="{FXS}" foreground="{DIM}">  {fmt_bytes(dev["size"])}'
               f'  {kind}</span><span font="{FXS}">{tstr}</span>')
        d = io.get(dev['dev'])
        if d:
            qcol = W.CRIT if d['queue'] > 8 else (W.WARN if d['queue'] > 2 else DIM)
            p.L(f"devio{di}",
                   f'<span font="{FXS}" foreground="{DIM}">   r </span>'
                   f'<span font="{FXS}" foreground="{W.CAT[0]}">{fmt_rate(d["r_bps"])}</span>'
                   f'<span font="{FXS}" foreground="{DIM}">  w </span>'
                   f'<span font="{FXS}" foreground="{W.CAT[1]}">{fmt_rate(d["w_bps"])}</span>'
                   f'<span font="{FXS}" foreground="{DIM}">   q</span>'
                   f'<span font="{FXS}" foreground="{qcol}">{d["queue"]}</span>'
                   f'<span font="{FXS}" foreground="{DIM}">   await '
                   f'{d["r_await"]:.1f}/{d["w_await"]:.1f}ms</span>')
        else:
            p.L(f"devio{di}", f'<span font="{FXS}" foreground="{DIM}">   idle</span>')

        # SMART: wear, written volume and defect counters. The panel used
        # to show only capacity and temperature, which says nothing about
        # whether the drive is dying.
        sm = (s.get('smart') or {}).get(dev['dev'])
        if sm:
            bits = []
            life = sm.get('life_pct')
            if isinstance(life, (int, float)):
                bits.append(kv("life", f'{life:.0f}%',
                                W.CRIT if life <= 10 else
                                (W.WARN if life <= 25 else W.OK), FXS, FXS))
            if sm.get('bytes_written'):
                # "~" when the unit was inferred rather than stated by the
                # drive, so an approximate figure never reads as measured.
                pre = '~' if sm.get('writes_inferred') else ''
                bits.append(kv("  written",
                                pre + fmt_bytes(sm['bytes_written']),
                                WHITE, FXS, FXS))
            elif sm.get('writes_raw') is not None:
                # The drive counts writes but will not say in what unit, so
                # show the counter rather than silently omitting the line —
                # a missing figure looks like a drive that never writes.
                bits.append(kv("  written", f'{sm["writes_raw"]:,}?',
                                W.INK_DIM, FXS, FXS))
            defects = ((sm.get('reallocated') or 0) + (sm.get('pending') or 0) +
                       (sm.get('media_errors') or 0))
            if defects:
                bits.append(kv("  defects", f'{defects}', W.CRIT, FXS, FXS))
            elif sm.get('healthy'):
                bits.append(f'<span font="{FXS}" foreground="{W.OK}">  healthy</span>')
            if sm.get('crit_temp_time'):
                bits.append(f'<span font="{FXS}" foreground="{W.WARN}">  '
                            f'{sm["crit_temp_time"]}m over temp</span>')
            p.L(f"devsmart{di}", ''.join(bits))
            p.vis(f"devsmart{di}", True)
        else:
            p.vis(f"devsmart{di}", False)

        p.vis(f"dev{di}", True)
        p.vis(f"devio{di}", True)
        di += 1

        if total_parts <= MAX_MOUNTS:
            shown, quiet = dev['parts'], []          # they all fit — show all
        else:
            shown = [pt for pt in dev['parts'] if _notable(pt, dev)]
            quiet = [pt for pt in dev['parts'] if not _notable(pt, dev)]
        for part in shown:
            if mi >= MAX_MOUNTS:
                break
            rb = p._wid.get(f"mnt{mi}")
            if not rb:
                break
            name = part['label'] or part['mount']
            if len(name) > 16:
                name = '…' + name[-15:]
            rb.set(f"  {name}",
                   f'{fmt_bytes(part["used"])}/{fmt_bytes(part["total"])}',
                   part['pct'], W.status_color(part['pct']))
            p.vis(f"mnt{mi}", True)
            mi += 1

        # Eight near-empty system partitions were pushing everything else
        # off the panel. They are collapsed to one line and expand the
        # moment any of them starts to fill.
        if quiet:
            worst = max(quiet, key=lambda p: p['pct'])
            p.L(f"devq{di-1}",
                   f'<span font="{FXS}" foreground="{DIM}">   +{len(quiet)} quiet '
                   f'partitions  ·  largest {worst["mount"]} '
                   f'{worst["pct"]:.0f}%</span>')
            p.vis(f"devq{di-1}", True)
        else:
            p.vis(f"devq{di-1}", False)

    for i in range(di, MAX_DEVICES):
        p.vis(f"dev{i}", False)
        p.vis(f"devio{i}", False)
        p.vis(f"devsmart{i}", False)
        p.vis(f"devq{i}", False)
    for i in range(mi, MAX_MOUNTS):
        p.vis(f"mnt{i}", False)

    rd = sum(d['r_bps'] for d in io.values())
    wr = sum(d['w_bps'] for d in io.values())
    busiest = max(io.items(), key=lambda kv: kv[1]['r_bps'] + kv[1]['w_bps'],
                  default=(None, None))
    who = busiest[0] if busiest[0] and (rd + wr) > 65536 else 'idle'
    p.L("io_hdr",
           f'<span font="{FXS}" foreground="{DIM}">all disks  </span>'
           f'<span font="{FS}" foreground="{W.CAT[0]}">↓ {fmt_rate(rd)}</span>'
           f'<span font="{FS}" foreground="{W.CAT[1]}">   ↑ {fmt_rate(wr)}</span>'
           f'<span font="{FXS}" foreground="{DIM}">   {who}</span>')
    sp = p._wid.get("io_spark")
    if sp:
        sp.push(rd, wr)
