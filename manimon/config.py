#!/usr/bin/env python3
"""
maniMon — configuration.

Everything site-specific lives here and nowhere else. The rest of the code
reads `CFG` and never hardcodes a path, a binary name or a service unit.

    python3 config.py            show the resolved configuration and its source
    python3 config.py --sample   print a starter config you can redirect to disk

WHERE THE CONFIG COMES FROM
───────────────────────────
Later sources win, and every one of them is optional — with no config at all
maniMon still runs and shows CPU, memory, GPU, disks, network, sensors and the
metric store. The job-accounting and project sections simply stay hidden,
because a section with nothing behind it is worse than no section.

    1. the defaults below
    2. /etc/manimon/config.toml          (or .json)
    3. $XDG_CONFIG_HOME/manimon/config.toml, else ~/.config/manimon/config.toml
    4. $MANIMON_CONFIG                   (explicit path, wins over everything)

TOML is read with the standard library's tomllib (Python 3.11+). On older
Pythons, or if you simply prefer it, use config.json instead — same keys.

© Manish Jagdish Thatte
"""

import json
import os
import re
import sys

try:                                   # Python 3.11+
    import tomllib
except ImportError:                    # pragma: no cover
    tomllib = None


# ═══════════════════════════════════════════════════════════════════════════════
#  Defaults — deliberately generic. Nothing here names a particular machine.
# ═══════════════════════════════════════════════════════════════════════════════
DEFAULTS = {
    # Where the metric store and UI state live.
    'state_dir': '~/.local/state/manimon',

    # ── Job accounting ──────────────────────────────────────────────────────
    # A process counts as a "job" if its executable is in `bins`, or if it is a
    # python/interpreter process whose command line mentions one of `roots`.
    # Leave `roots` empty and only the named binaries are tracked.
    'jobs': {
        'roots': [],
        'bins': [
            # Quantum ESPRESSO
            'pw.x', 'ph.x', 'cp.x', 'dos.x', 'projwfc.x', 'pp.x', 'bands.x',
            'q2r.x', 'matdyn.x', 'neb.x', 'epw.x',
            # molecular dynamics
            'lmp', 'lmp_mpi', 'lmp_kokkos', 'gmx', 'gmx_mpi', 'namd2',
            # electronic structure
            'yambo', 'ypp', 'abinit', 'anaddb', 'gpaw', 'gpaw-python',
            'octopus', 'siesta', 'vasp', 'cp2k', 'orca', 'nwchem',
            # electromagnetics / FEM / circuits
            'meep', 'sfepy', 'ngspice', 'elmersolver',
        ],
        'mpi_launchers': ['mpirun', 'mpiexec', 'orterun', 'srun', 'orted'],
        # Command-line pattern that also marks a process as a job. The first
        # capture group, when present, becomes the job's display name.
        'id_regex': r'(?:--run|--case|--id)[= ]([A-Za-z0-9_.\-]+)',
    },

    # ── Optional sections. Omit or leave empty and the panel hides them. ────
    'repo': {
        'path': None,                  # a git working tree to watch
    },
    'backups': {
        'log_dir': None,               # directory holding the log files below
        'jobs': [],                    # [{log, label, stale_days}]
    },
    'campaign': {
        # A long-running study: a directory of per-run folders, optionally with
        # a markdown scoreboard. Entirely optional.
        'root': None,
        'layers': [],
        # The campaign has a name; this tool does not know it. Shown as the
        # section heading. A hardcoded one lived here until 21 Aug 2026 and
        # went on naming a campaign that had been retired a fortnight earlier.
        'label': 'CAMPAIGN',
        # Subdirectory of `root` holding one directory per run.
        'results_dir': 'output',
        # Markdown scoreboard, relative to `root`. Having none is normal and
        # not a fault: without one the section reports what is on disk rather
        # than what a ledger claims, which is the weaker but honest reading.
        'status_file': 'STATUS.md',
    },
    'venvs': [],                       # python venvs to report versions from

    # ── Services worth a status dot ─────────────────────────────────────────
    # (label, unit, scope). Unit names must be CANONICAL, not aliases: asking
    # about "smartd" returns Id=smartmontools.service and a name lookup then
    # misses. Scope matters too — a user unit queried against the system
    # manager reports as absent.
    'services': [
        ['smartd',  'smartmontools.service', 'system'],
        ['sensors', 'manimon-sensors.timer', 'system'],
        ['cron',    'cron.service',          'system'],
        ['network', 'NetworkManager.service', 'system'],
    ],

    # ── Sensor sampler ──────────────────────────────────────────────────────
    'sensors': {
        'runtime_dir': '/run/manimon-sensors',
        'ipmi_every': 30,
        'nvme_every': 300,
        'sata_every': 300,
    },

    # ── Thresholds used for colour and for "how often was I over" ───────────
    'limits': {
        'gpu_junction': 85,            # °C — the number a thermal policy names
        'gpu_mem': 95,
        'cpu_temp': 85,
        'nvme_temp': 70,
        'disk_temp': 60,
    },

    # ── Memory ──────────────────────────────────────────────────────────────
    # How many memory channels the PROCESSOR can address.
    #
    # dmidecode reports the board's DIMM slots, and that is the ceiling you can
    # reach by buying RDIMMs. It cannot report the processor's channel count,
    # and there is no portable place to read it — so it is not guessed. Left
    # unset, maniMon reports only the ceiling it can observe.
    #
    # Set it to also see what the board itself is giving up: EPYC 9004 is 12,
    # Xeon Scalable 8, Threadripper Pro 8, desktop Ryzen and Core 2. A hardcoded
    # 12 lived here until 19 Aug 2026 and reported a 461 GB/s ceiling on an
    # eight-slot board, which no amount of memory could ever have reached.
    'memory': {
        'cpu_channels': None,
    },

    # ── Panels ──────────────────────────────────────────────────────────────
    'panels': {
        'width': 420,
        'update_ms': 2000,
        'theme': 'light',              # 'light' | 'dark'
    },
}


def _deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_file(path):
    if not path or not os.path.exists(path):
        return None
    try:
        if path.endswith('.toml'):
            if tomllib is None:
                print(f"config: {path} needs Python 3.11+ for tomllib; "
                      f"use config.json instead", file=sys.stderr)
                return None
            with open(path, 'rb') as f:
                return tomllib.load(f)
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        # A broken config must not take the monitor down, but it must be loud:
        # silently falling back to defaults would look like the config was
        # ignored for no reason.
        print(f"config: ignoring {path} — {e}", file=sys.stderr)
        return None


def _candidates():
    xdg = os.environ.get('XDG_CONFIG_HOME') or os.path.expanduser('~/.config')
    out = []
    for d in ('/etc/manimon', os.path.join(xdg, 'manimon')):
        out += [os.path.join(d, 'config.toml'), os.path.join(d, 'config.json')]
    explicit = os.environ.get('MANIMON_CONFIG')
    if explicit:
        out.append(os.path.expanduser(explicit))
    return out


def load():
    cfg = dict(DEFAULTS)
    sources = []
    for path in _candidates():
        data = _load_file(path)
        if data:
            cfg = _deep_merge(cfg, data)
            sources.append(path)
    cfg['_sources'] = sources
    cfg['state_dir'] = os.path.expanduser(cfg['state_dir'])
    for key in ('root',):
        if cfg['campaign'].get(key):
            cfg['campaign'][key] = os.path.expanduser(cfg['campaign'][key])
    if cfg['repo'].get('path'):
        cfg['repo']['path'] = os.path.expanduser(cfg['repo']['path'])
    if cfg['backups'].get('log_dir'):
        cfg['backups']['log_dir'] = os.path.expanduser(cfg['backups']['log_dir'])
    cfg['jobs']['roots'] = [os.path.expanduser(r) for r in cfg['jobs']['roots']]
    return cfg


CFG = load()

# Flattened names the modules use, so no module has to know the config shape.
STATE_DIR     = CFG['state_dir']
JOB_ROOTS     = tuple(CFG['jobs']['roots'])
JOB_BINS      = set(CFG['jobs']['bins'])
MPI_LAUNCHERS = set(CFG['jobs']['mpi_launchers'])
JOB_ID_RE     = re.compile(CFG['jobs']['id_regex']) if CFG['jobs'].get('id_regex') else None
SERVICES      = [tuple(s) for s in CFG['services']]
REPO_PATH     = CFG['repo'].get('path')
BACKUP_DIR    = CFG['backups'].get('log_dir')
BACKUP_JOBS   = [(j['log'], j['label'], j.get('stale_days', 7))
                 for j in CFG['backups'].get('jobs', [])]
CAMPAIGN_ROOT   = CFG['campaign'].get('root')
CAMPAIGN_LABEL  = CFG['campaign'].get('label') or 'CAMPAIGN'
CAMPAIGN_RUNS   = CFG['campaign'].get('results_dir') or 'output'
CAMPAIGN_STATUS = CFG['campaign'].get('status_file') or ''
LAYERS          = list(CFG['campaign'].get('layers') or [])
VENVS         = [os.path.expanduser(v) for v in CFG.get('venvs', [])]
LIMITS        = CFG['limits']
CPU_CHANNELS  = CFG['memory'].get('cpu_channels')
SENSOR_DIR    = CFG['sensors']['runtime_dir']
PANEL_WIDTH   = CFG['panels']['width']
UPDATE_MS     = CFG['panels']['update_ms']
THEME         = CFG['panels']['theme']


SAMPLE = '''# maniMon configuration — TOML.
# Save as ~/.config/manimon/config.toml. Every key is optional.
#
# © Manish Jagdish Thatte

state_dir = "~/.local/state/manimon"

[jobs]
# Paths that mark an interpreter process as a job worth tracking.
roots = ["~/work/simulations"]
# Executables always treated as jobs (added to the built-in physics list).
# bins = ["my_solver"]

[repo]
# A git working tree to show branch, dirty state and unpushed commits for.
path = "~/work/project"

[backups]
log_dir = "~/work/tools"
jobs = [
  { log = "backup_main.log", label = "MAIN_DRIVE", stale_days = 7 },
]

[campaign]
# A long study: an output/ directory of per-run folders, optionally with a
# markdown scoreboard. Leave root unset and the section disappears entirely.
root   = "~/work/campaign"
layers = ["L0", "L1", "L2"]
# Section heading — name your campaign, the tool will not guess.
label  = "CAMPAIGN"
# Where the per-run directories live, relative to root.
results_dir = "output"
# Optional markdown scoreboard. Omit it and the bars report runs found on
# disk instead of a ledger's claims.
status_file = "STATUS.md"

[limits]
gpu_junction = 85
nvme_temp    = 70

[memory]
# Your PROCESSOR's memory-channel count. The board's slot count is read from
# dmidecode; this is not discoverable, so it is not guessed. Set it and the
# panel also shows what the board is giving up against the CPU's capability.
# EPYC 9004 = 12, Xeon Scalable = 8, Threadripper Pro = 8, Ryzen/Core = 2.
# cpu_channels = 12

[panels]
width     = 420
theme     = "light"
update_ms = 2000

# Services worth a status dot: [label, canonical-unit, scope]
services = [
  ["smartd",  "smartmontools.service",  "system"],
  ["sensors", "manimon-sensors.timer",  "system"],
  ["cron",    "cron.service",           "system"],
  ["network", "NetworkManager.service", "system"],
]
'''


def show_sample():
    """Print a starter configuration file."""
    print(SAMPLE)
    return 0


def show_resolved():
    """Print the merged configuration and every path that was searched."""
    src = CFG['_sources'] or ['(none — using built-in defaults)']
    print("maniMon configuration")
    print("=" * 60)
    print("sources:")
    for s in src:
        print(f"  {s}")
    print()
    shown = {k: v for k, v in CFG.items() if k != '_sources'}
    print(json.dumps(shown, indent=2, default=str))
    print()
    print("searched:")
    for p in _candidates():
        print(f"  {'FOUND  ' if os.path.exists(p) else '       '}{p}")
    return 0


if __name__ == '__main__':
    sys.exit(show_sample() if '--sample' in sys.argv else show_resolved())
