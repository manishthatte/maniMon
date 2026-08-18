"""
Chassis: BMC fans, board temperatures and rails.

Both halves of one section live here: the widgets it creates and the code that
fills them. They used to sit 150 lines apart in a single 850-line module.
"""

from .. import widgets as W
from ..window import *   # noqa: F401,F403
from ..markup import kv
from ...util import fmt_bytes, fmt_rate, fmt_elapsed

def build(p):
        # 6. Chassis / BMC -----------------------------------------------------
        # Hidden entirely until sensord publishes, so a machine without the
        # sampler deployed shows no empty scaffolding.
        p._wid["chassis_head"] = p.head("❉", "CHASSIS  ·  BMC", TEAL)
        p.lbl("bmc_fans")
        p.lbl("bmc_temps")
        p.lbl("bmc_power")
        p.lbl("bmc_absent")


def refresh(p, s):
    """
    Board-level sensors from the BMC: chassis fans, VRM/DIMM/inlet
    temperatures, voltage rails, PSU draw. None of this tier was read at
    all before 18 Aug 2026 — the BMC has always been there, the monitor
    simply never asked it.
    """
    b = s.get('bmc') or {}
    head = p._wid.get("chassis_head")

    if not b.get('present'):
        # Show one quiet line explaining how to turn it on, rather than an
        # empty section or a silent omission.
        if head:
            head.set_visible(True)
            head.set_no_show_all(False)
        p.L("bmc_absent",
               f'<span font="{FXS}" foreground="{DIM}">  sampler not running · '
               f'sudo systemctl start manimon-sensors</span>')
        for k in ("bmc_fans", "bmc_temps", "bmc_power"):
            p.vis(k, False)
        p.vis("bmc_absent", True)
        return

    p.vis("bmc_absent", False)
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
        p.L("bmc_fans",
               kv("fans", f'{b["fan_count"]} · {spread} rpm', fcol) +
               (f'<span font="{FXS}" foreground="{W.CRIT}">   {len(dead)} DEAD: '
                f'{",".join(dead)[:18]}</span>' if dead else '') + age_txt)
        p.vis("bmc_fans", True)
    else:
        p.vis("bmc_fans", False)

    temps = b.get('temps') or []
    if temps:
        tmax = b.get('temp_max') or 0
        p.L("bmc_temps",
               kv("board", f'{len(temps)} sensors · max {tmax:.0f}°',
                   W.temp_color(tmax, 60, 80)) +
               f'<span font="{FXS}" foreground="{DIM}">  '
               f'{(b.get("temp_hottest") or "")[:14]}</span>')
        p.vis("bmc_temps", True)
    else:
        p.vis("bmc_temps", False)

    bits = []
    if b.get('power'):
        bits.append(kv("PSU", f'{b["power"]:.0f} W', W.CAT[1]))
    rails = b.get('rails_off_nominal') or []
    if rails:
        bits.append(f'<span font="{FXS}" foreground="{W.WARN}">   rails off: '
                    f'{",".join(rails)[:20]}</span>')
    elif b.get('volts'):
        bits.append(f'<span font="{FXS}" foreground="{W.OK}">   '
                    f'{len(b["volts"])} rails nominal</span>')
    if bits:
        p.L("bmc_power", ''.join(bits))
        p.vis("bmc_power", True)
    else:
        p.vis("bmc_power", False)
