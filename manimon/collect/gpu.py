"""
GPU inventory from amdgpu sysfs, and which processes hold a card open.
"""

import os, glob

from ..util import rf, ri, read_temps


# ═══════════════════════════════════════════════════════════════════════════════
#  GPU
# ═══════════════════════════════════════════════════════════════════════════════
def gpus():
    """
    Enumerate real compute GPUs.

    NOTE: card0 on this machine is the ASPEED BMC VGA, card1 is the W7900.
    Filtering on gpu_busy_percent is what separates them. Count is dynamic —
    one card or two both render correctly.
    """
    out = []
    for card in sorted(glob.glob('/sys/class/drm/card[0-9]')):
        dev = f'{card}/device'
        if not os.path.exists(f'{dev}/gpu_busy_percent'):
            continue
        hw = next(iter(glob.glob(f'{dev}/hwmon/hwmon*')), None)
        t = read_temps(hw) if hw else {}
        vused = ri(f'{dev}/mem_info_vram_used', 0)
        vtot  = ri(f'{dev}/mem_info_vram_total', 1)
        power = ri(f'{hw}/power1_average', 0) / 1e6 if hw else 0.0
        cap   = ri(f'{hw}/power1_cap', 0) / 1e6 if hw else 0.0
        fan   = ri(f'{hw}/fan1_input', 0) if hw else 0
        fanmx = ri(f'{hw}/fan1_max', 0) if hw else 0
        sclk  = ri(f'{hw}/freq1_input', 0) // 1_000_000 if hw else 0
        mclk  = ri(f'{hw}/freq2_input', 0) // 1_000_000 if hw else 0
        busy  = ri(f'{dev}/gpu_busy_percent', 0)
        membusy = ri(f'{dev}/mem_busy_percent', 0)
        out.append({
            'card': os.path.basename(card),
            'name': rf(f'{dev}/product_name') or 'Radeon Pro W7900',
            'busy': busy,
            'mem_busy': membusy,
            'vram_used_gb': vused / 1_073_741_824,
            'vram_total_gb': vtot / 1_073_741_824,
            'vram_pct': vused / max(vtot, 1) * 100,
            'temp_edge': t.get('edge', 0.0),
            'temp_junction': t.get('junction', 0.0),
            'temp_mem': t.get('mem', 0.0),
            'power': round(power, 1),
            'power_cap': round(cap, 1),
            'fan_rpm': fan,
            'fan_pct': round(fan / fanmx * 100, 0) if fanmx else 0,
            'sclk': sclk,
            'mclk': mclk,
            'link_speed': rf(f'{dev}/current_link_speed', '?'),
            'link_width': rf(f'{dev}/current_link_width', '?'),
            'link_speed_max': rf(f'{dev}/max_link_speed', '?'),
            'link_width_max': rf(f'{dev}/max_link_width', '?'),
            # estimates against W7900 peak figures
            'tflops': round(busy / 100 * 61.3, 1),
            'bw_gbs': round(membusy / 100 * 864, 0),
        })
    return out


def gpu_clients():
    """
    PIDs holding a render node open — i.e. what is actually using the GPU.
    Cheap enough for the SLOW tier; only our own processes are visible.
    """
    pids = {}
    for fd in glob.glob('/proc/[0-9]*/fd/*'):
        try:
            tgt = os.readlink(fd)
        except Exception:
            continue
        if '/dev/dri/renderD' in tgt or '/dev/dri/card' in tgt:
            pid = fd.split('/')[2]
            if pid not in pids:
                comm = rf(f'/proc/{pid}/comm')
                if comm:
                    pids[pid] = comm
    return pids
