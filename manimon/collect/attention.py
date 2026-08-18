"""
The attention engine: everything that might need a human, ranked by severity.

This is the one place that decides what is worth interrupting someone for, so
it is deliberately kept apart from the readers that supply the raw numbers.
"""

import os, json, time

from ..util import fmt_age, fmt_bytes, fmt_elapsed
from ..config import STATE_DIR


# ═══════════════════════════════════════════════════════════════════════════════
#  Attention engine
# ═══════════════════════════════════════════════════════════════════════════════
SEV_CRIT, SEV_WARN, SEV_INFO, SEV_OK = 0, 1, 2, 3
STATE_FILE = f'{STATE_DIR}/seen.json'


def _load_state():
    try:
        with open(STATE_FILE) as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_state(state):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = STATE_FILE + '.tmp'
        with open(tmp, 'w') as fh:
            json.dump(state, fh)
        os.replace(tmp, STATE_FILE)
    except Exception:
        pass


def acknowledge(key):
    """Mark one item (or '*' for all currently known) as seen."""
    st = _load_state()
    st[key] = time.time()
    cutoff = time.time() - 7 * 86400
    _save_state({k: v for k, v in st.items() if v > cutoff})


def attention(snap):
    """
    Aggregate everything that might need Manish's eyes, ranked by severity.
    Items carry a stable `key` so a click can acknowledge them.
    """
    st = _load_state()
    items = []

    def add(key, sev, icon, text, ackable=True):
        if ackable and key in st:
            return
        items.append({'key': key, 'sev': sev, 'icon': icon,
                      'text': text, 'ackable': ackable})

    # Failed and finished simulations
    for r in snap.get('recent', []):
        stamp = int(r['mtime'])
        if r['rc'] not in (0, None):
            add(f'simfail:{r["sim_id"]}:{stamp}', SEV_CRIT, '✗',
                f'{r["sim_id"]} FAILED rc={r["rc"]} · {fmt_age(r["age"])}')
        else:
            dur = f' in {fmt_elapsed(r["seconds"])}' if r.get('seconds') else ''
            add(f'simdone:{r["sim_id"]}:{stamp}', SEV_WARN, '✓',
                f'{r["sim_id"]} finished{dur} · {fmt_age(r["age"])}')

    # Dead tmux panes
    for p in snap.get('tmux', []):
        if p['dead']:
            add(f'tmuxdead:{p["session"]}:{p["window"]}', SEV_CRIT, '▣',
                f'{p["session"]}:{p["window_name"]} pane exited')

    # Backups
    for b in snap.get('backups', []):
        if not b['mounted']:
            add(f'nomount:{b["label"]}', SEV_WARN, '⏏',
                f'{b["label"]} not mounted')
        elif b['stale']:
            add(f'stale:{b["label"]}', SEV_WARN, '⏏',
                f'{b["label"]} backup {fmt_age(b["age"])}')
        elif b['ok'] is False:
            add(f'bkpfail:{b["label"]}', SEV_CRIT, '⏏',
                f'{b["label"]} backup FAILED')

    # Filesystems filling up. An unmounted LV is synthesised at 100% so it
    # shows its full size on the storage bar — but it is not a filesystem
    # running out of room, and alarming on it would be a permanent false CRIT.
    for dev in snap.get('disks', []):
        for part in dev['parts']:
            if part.get('unused_lv'):
                continue
            if part['pct'] >= 92:
                add(f'full:{part["mount"]}', SEV_CRIT, '◫',
                    f'{part["mount"]} {part["pct"]:.0f}% full', ackable=False)
            elif part['pct'] >= 85:
                add(f'fill:{part["mount"]}', SEV_WARN, '◫',
                    f'{part["mount"]} {part["pct"]:.0f}% full', ackable=False)

    # Swap in use on a 251 GB box means something is wrong
    mem = snap.get('mem') or {}
    if mem.get('swap_used_gb', 0) > 1:
        add('swap', SEV_WARN, '▦',
            f'swap in use: {mem["swap_used_gb"]:.1f} GB', ackable=False)

    # Thermals
    for g in snap.get('gpus', []):
        if g['temp_junction'] >= 95:
            add(f'gputemp:{g["card"]}', SEV_CRIT, '◉',
                f'{g["card"]} junction {g["temp_junction"]:.0f}°C', ackable=False)

    # ── ECC: the earliest warning that a DIMM is failing ─────────────────────
    ec = snap.get('ecc') or {}
    if ec.get('present'):
        if ec.get('ue'):
            add(f'ecc_ue:{ec["ue"]}', SEV_CRIT, '▨',
                f'{ec["ue"]} UNCORRECTABLE ECC error(s) — memory is failing')
        elif ec.get('ce'):
            add(f'ecc_ce:{ec["ce"]}', SEV_WARN, '▨',
                f'{ec["ce"]} correctable ECC error(s)')

    # ── Drive health, from SMART ─────────────────────────────────────────────
    for dev, s in (snap.get('smart') or {}).items():
        if s.get('healthy') is False:
            add(f'smartfail:{dev}', SEV_CRIT, '◈',
                f'{dev} SMART health FAILED — replace it', ackable=False)
        if s.get('reallocated'):
            add(f'realloc:{dev}:{s["reallocated"]}', SEV_WARN, '◈',
                f'{dev} {s["reallocated"]} reallocated sector(s)')
        if s.get('pending'):
            add(f'pending:{dev}:{s["pending"]}', SEV_WARN, '◈',
                f'{dev} {s["pending"]} pending sector(s)')
        if s.get('media_errors'):
            add(f'mediaerr:{dev}:{s["media_errors"]}', SEV_WARN, '◈',
                f'{dev} {s["media_errors"]} media error(s)')
        life = s.get('life_pct')
        if isinstance(life, (int, float)) and life <= 10:
            add(f'life:{dev}', SEV_WARN, '◈',
                f'{dev} {life:.0f}% life remaining', ackable=False)
        # Seconds spent above the drive's own thermal limit. Non-zero is the
        # answer to "why was that run slower than the last one".
        if s.get('crit_temp_time'):
            add(f'nvmethrottle:{dev}', SEV_WARN, '◈',
                f'{dev} {s["crit_temp_time"]} min above critical temp', ackable=False)

    # ── Chassis: a fan at 0 while its neighbours spin is a dead fan ──────────
    bm = snap.get('bmc') or {}
    for fan in bm.get('dead_fans', []):
        add(f'deadfan:{fan}', SEV_CRIT, '❉', f'chassis fan {fan} reads 0 RPM',
            ackable=False)
    for rail in bm.get('rails_off_nominal', []):
        add(f'rail:{rail}', SEV_WARN, '⚡', f'{rail} more than 5% off nominal',
            ackable=False)

    # ── GPU throttling, only when the flag is trustworthy ───────────────────
    for card, gm in (snap.get('gpu_metrics') or {}).items():
        if gm.get('supported') and gm.get('throttled'):
            why = ', '.join(gm.get('throttle_reasons') or []) or 'unknown'
            add(f'throttle:{card}', SEV_WARN, '◉',
                f'{card} throttling: {why}', ackable=False)

    # ── An allocated logical volume nobody is using ─────────────────────────
    for dev in snap.get('disks', []):
        for part in dev['parts']:
            if part.get('unused_lv'):
                add(f'unusedlv:{part["mount"]}', SEV_INFO, '◫',
                    f'{fmt_bytes(part["total"])} LV allocated, not mounted')

    # Idle GPU while the campaign still has pending work
    camp = snap.get('campaign') or {}
    gpu_busy = max((g['busy'] for g in snap.get('gpus', [])), default=0)
    if camp.get('pending', 0) > 0 and gpu_busy < 5 and not snap.get('sims'):
        add('gpuidle', SEV_INFO, '◉',
            f'GPU idle · {camp["pending"]} sims pending', ackable=False)

    # Repo
    rp = snap.get('repo') or {}
    if rp.get('unpushed'):
        add('unpushed', SEV_WARN, '⎇',
            f'{rp["unpushed"]} unpushed commit(s)', ackable=False)
    if rp.get('dirty'):
        add('dirty', SEV_INFO, '⎇',
            f'{rp["dirty"]} uncommitted file(s)', ackable=False)

    # Journal
    jn = snap.get('journal') or {}
    if jn.get('errors', 0) > 0:
        add(f'journal:{jn["errors"]}', SEV_INFO, '⚠',
            f'{jn["errors"]} journal errors this boot')

    items.sort(key=lambda i: (i['sev'], i['text']))
    return items
