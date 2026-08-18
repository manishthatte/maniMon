"""
tmux sessions, and an optional campaign of batch runs on disk.
"""

import os, re, glob, json, time

from ..util import rf, sh
from ..config import CAMPAIGN_ROOT, LAYERS


# ═══════════════════════════════════════════════════════════════════════════════
#  tmux — sessions, windows, panes, live output
# ═══════════════════════════════════════════════════════════════════════════════
class TmuxCollector:
    FMT = ('#{session_name}\t#{window_index}\t#{window_name}\t#{pane_id}\t'
           '#{pane_pid}\t#{pane_current_command}\t#{pane_dead}\t'
           '#{window_active}\t#{session_attached}')

    def __init__(self):
        self._cap = {}

    def read(self, capture=True):
        raw = sh(f'tmux list-panes -a -F "{self.FMT}" 2>/dev/null', timeout=2)
        if not raw:
            return []
        panes = []
        for line in raw.split('\n'):
            f = line.split('\t')
            if len(f) < 9:
                continue
            pane_id = f[3]
            last = ''
            if capture:
                last = self._capture(pane_id)
            panes.append({
                'session': f[0],
                'window': f[1],
                'window_name': f[2],
                'pane': pane_id,
                'pid': int(f[4]) if f[4].isdigit() else 0,
                'cmd': f[5],
                'dead': f[6] == '1',
                'active': f[7] == '1',
                'attached': f[8] not in ('', '0'),
                'last_line': last,
            })
        return panes

    def _capture(self, pane_id, max_age=4.0):
        val, ts = self._cap.get(pane_id, ('', 0.0))
        if val and time.monotonic() - ts < max_age:
            return val
        out = sh(f'tmux capture-pane -p -t {pane_id} -S -6 2>/dev/null', timeout=2)
        line = ''
        for ln in reversed(out.split('\n')):
            if ln.strip():
                line = ln.strip()[:60]
                break
        self._cap[pane_id] = (line, time.monotonic())
        return line


# ═══════════════════════════════════════════════════════════════════════════════
#  Campaign — an optional long-running study
# ═══════════════════════════════════════════════════════════════════════════════
# LAYERS comes from config (campaign.layers). Empty by default, which hides
# the campaign section rather than showing an empty scoreboard.


def campaign():
    """
    Progress of a long-running study, when one is configured.

    Source-of-truth policy:

      STATUS.md is authoritative for the SCOREBOARD. confirmed / partial /
      pending are human judgements about whether a result actually validates a
      claim; no file on disk records that, so the filesystem cannot supply it.

      The filesystem is authoritative for EXECUTION. Whether a sim has run, when,
      and with what exit code comes from the output directory and
      .runner_status.json, which are generated and cannot drift.

    These answer different questions, so subtracting one from the other would be
    a category error — a sim can have run and still not be confirmed. What IS a
    genuine inconsistency, and is therefore flagged, is a layer the ledger calls
    confirmed that has no output artefacts at all: a claim with nothing behind it.
    """
    ledger = {}
    totals = {'scripts': 0, 'confirmed': 0, 'partial': 0, 'pending': 0}
    if not CAMPAIGN_ROOT:                      # no campaign configured: hide the section
        return {}
    status_md = f'{CAMPAIGN_ROOT}/STATUS.md'
    num = r'\s*\**(\d+)\**\s*'
    row = re.compile(r'^\|\s*\**(L\d)\**[^|]*\|' + num + r'\|' + num +
                     r'\|' + num + r'\|' + num + r'\|')
    tot = re.compile(r'^\|\s*\**Total\**\s*\|' + num + r'\|' + num +
                     r'\|' + num + r'\|' + num + r'\|', re.I)
    for line in rf(status_md).split('\n'):
        line = line.strip()
        m = row.match(line)
        if m:
            g = [int(x) for x in m.groups()[1:]]
            ledger[m.group(1)] = {'scripts': g[0], 'confirmed': g[1],
                                  'partial': g[2], 'pending': g[3]}
            continue
        m = tot.match(line)
        if m:
            g = [int(x) for x in m.groups()]
            totals = {'scripts': g[0], 'confirmed': g[1],
                      'partial': g[2], 'pending': g[3]}

    # Execution evidence from the output tree
    ran, failed, failed_ids = {}, 0, []
    for d in sorted(glob.glob(f'{CAMPAIGN_ROOT}/output/*')):
        if not os.path.isdir(d):
            continue
        sim = os.path.basename(d)
        lay = sim[:2].upper().replace('_', '')
        # Any real output file counts: engines emit .dat/.json/.log/.png/.csv
        artefacts = [f for f in glob.glob(f'{d}/*') if os.path.isfile(f)]
        rs = f'{d}/.runner_status.json'
        rc = None
        if os.path.exists(rs):
            try:
                rc = json.load(open(rs)).get('returncode')
            except Exception:
                rc = None
        if rc not in (None, 0):
            failed += 1
            failed_ids.append(sim)
        if artefacts:
            ran[lay] = ran.get(lay, 0) + 1

    out = []
    for lay in LAYERS:
        led = ledger.get(lay, {'scripts': 0, 'confirmed': 0,
                               'partial': 0, 'pending': 0})
        out.append({
            'layer': lay,
            'scripts': led['scripts'],
            'confirmed': led['confirmed'],
            'partial': led['partial'],
            'pending': led['pending'],
            'ran': ran.get(lay, 0),
            # genuine inconsistency: ledger claims results, disk has nothing
            'unbacked': led['confirmed'] + led['partial'] > 0 and ran.get(lay, 0) == 0,
        })
    return {
        'layers': out,
        'scripts': totals['scripts'] or sum(l['scripts'] for l in out),
        'confirmed': totals['confirmed'] or sum(l['confirmed'] for l in out),
        'partial': totals['partial'] or sum(l['partial'] for l in out),
        'pending': totals['pending'] or sum(l['pending'] for l in out),
        'ran': sum(ran.values()),
        'failed': failed,
        'failed_ids': failed_ids[:5],
    }


def recent_results(hours=24, limit=10):
    """Jobs that produced output recently — the 'finished' feed."""
    if not CAMPAIGN_ROOT:                      # no campaign configured
        return []
    cutoff = time.time() - hours * 3600
    out = []
    for d in glob.glob(f'{CAMPAIGN_ROOT}/output/*'):
        if not os.path.isdir(d):
            continue
        sim = os.path.basename(d)
        rs, rc, secs = f'{d}/.runner_status.json', None, None
        if os.path.exists(rs):
            try:
                js = json.load(open(rs))
                rc, secs = js.get('returncode'), js.get('seconds')
            except Exception:
                pass
        newest, size = None, 0
        for f in glob.glob(f'{d}/*.json') + glob.glob(f'{d}/*.log'):
            try:
                m = os.path.getmtime(f)
            except Exception:
                continue
            if newest is None or m > newest:
                newest, size = m, os.path.getsize(f)
        if newest is None or newest < cutoff:
            continue
        out.append({
            'sim_id': sim,
            'mtime': newest,
            'age': time.time() - newest,
            'rc': rc,
            'seconds': secs,
            'size': size,
            'ok': rc in (0, None),
        })
    out.sort(key=lambda r: -r['mtime'])
    return out[:limit]
