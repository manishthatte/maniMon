"""
The `manimon` command.

One entry point for what used to be six separate `python3 <file>.py` calls.
Subcommands import lazily, so `manimon report` never pays for GTK and works
perfectly well over SSH with no display.
"""

import argparse
import sys

from . import __version__


def _cmd_dump(a):
    from .collect import Collector
    import json, time
    c = Collector()
    c.tick(force_all=True)
    time.sleep(1.0)                      # second sample, so rates are non-zero
    snap = c.tick(force_all=True)
    if a.section:
        snap = {k: v for k, v in snap.items() if k in a.section}
    json.dump(snap, sys.stdout, indent=2, default=str)
    print()
    return 0


def _cmd_record(a):
    from .store.metrics import record_loop
    return record_loop()


def _cmd_report(a):
    from .store.metrics import report
    return report(a.hours)


def _cmd_info(a):
    from .store.metrics import Reader
    import json
    with Reader() as r:
        print(json.dumps(r.info(), indent=2, default=str))
    return 0


def _cmd_runs(a):
    from .store.runs import report
    return report(days=a.days, sim_id=a.id, active_only=a.active)


def _cmd_sensors(a):
    from .sensors.daemon import main as daemon_main
    argv = list(a.source)
    for flag in ('once', 'print', 'force', 'show', 'preflight'):
        if getattr(a, flag.replace('-', '_')):
            argv.append(f'--{flag}')
    return daemon_main(argv)


def _cmd_health(a):
    from .sensors.health import read_all
    import json
    d = read_all()
    print(json.dumps(d, indent=2, default=str))
    if not d['bmc'].get('present'):
        print(f"\nNOTE: {d['bmc'].get('reason')}", file=sys.stderr)
        print("      sudo systemctl start manimon-sensors.service", file=sys.stderr)
    return 0


def _cmd_config(a):
    from .config import show_sample, show_resolved
    return show_sample() if a.sample else show_resolved()


def _cmd_doctor(a):
    from .doctor import run
    return run(verbose=a.verbose)


def _cmd_panels(a):
    from .ui import launch
    return launch(a.action, side=a.side)


def _cmd_palette(a):
    from .ui.palette import _main
    return _main()


def _cmd_preview(a):
    from .ui.preview import main as preview_main
    return preview_main(a.out)


def build_parser():
    p = argparse.ArgumentParser(
        prog='manimon',
        description='A workstation monitor that keeps the numbers.',
        epilog='Start with `manimon doctor` if something is missing.')
    p.add_argument('-V', '--version', action='version',
                   version=f'maniMon {__version__}')
    sub = p.add_subparsers(dest='cmd', metavar='<command>')

    def add(name, fn, help, **kw):
        s = sub.add_parser(name, help=help, description=help, **kw)
        s.set_defaults(fn=fn)
        return s

    s = add('panels', _cmd_panels, 'start, stop or check the docked panels')
    s.add_argument('action', nargs='?', default='start',
                   choices=['start', 'stop', 'restart', 'status', 'ensure', 'exec'],
                   help="'ensure' restarts only what has died; 'exec' becomes "
                        "the panel itself and is what the systemd unit runs")
    s.add_argument('--side', choices=['left', 'right', 'both'], default='both',
                   help='which panel to act on (default: both)')

    add('record', _cmd_record, 'run the recorder in the foreground (systemd calls this)')

    s = add('report', _cmd_report, 'history: p95, dated peaks, kWh, time over threshold')
    s.add_argument('--hours', type=float, default=24.0, help='window (default: 24)')

    add('info', _cmd_info, 'metric store size, span and row counts')

    s = add('runs', _cmd_runs, 'what each recognised job cost')
    s.add_argument('--days', type=float, default=7, help='window (default: 7)')
    s.add_argument('--id', help='restrict to one job id')
    s.add_argument('--active', action='store_true', help='only runs still going')

    s = add('sensors', _cmd_sensors, 'privileged sensor sampler (root; the timer calls this)')
    # Declared individually rather than swept up as positionals: argparse treats
    # a leading -- as an option no matter what a positional says it accepts, so
    # a catch-all silently rejected `--preflight` at the one moment it mattered
    # — inside the installer, where nobody was watching the exit status.
    s.add_argument('--once', action='store_true', help='sample once and exit')
    s.add_argument('--print', action='store_true', dest='print',
                   help='dump the sampled JSON to stdout')
    s.add_argument('--force', action='store_true',
                   help='ignore the per-source rate limit (hand-testing only)')
    s.add_argument('--show', action='store_true',
                   help='show what is currently published, without sampling')
    s.add_argument('--preflight', action='store_true',
                   help='report which sensors this machine can actually read')
    s.add_argument('source', nargs='*',
                   help='limit to these sources (ipmi, nvme, sata, dimms)')

    add('health', _cmd_health, 'dump what the privileged sampler published')

    s = add('config', _cmd_config, 'resolved configuration, and where each part came from')
    s.add_argument('--sample', action='store_true', help='print a starter config file')

    s = add('doctor', _cmd_doctor, 'check the installation and say what is missing')
    s.add_argument('-v', '--verbose', action='store_true', help='show checks that passed')

    s = add('dump', _cmd_dump, 'one snapshot of everything, as JSON')
    s.add_argument('section', nargs='*', help='limit to these top-level sections')

    add('palette', _cmd_palette, 'colour contrast check (development)')
    s = add('preview', _cmd_preview, 'render a theme preview image (development)')
    s.add_argument('out', nargs='?', default='theme_preview.png')
    return p


def main(argv=None):
    p = build_parser()
    a = p.parse_args(argv)
    if not getattr(a, 'fn', None):
        p.print_help()
        return 0
    try:
        return a.fn(a) or 0
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:            # `manimon dump | head` is not an error
        return 0


if __name__ == '__main__':
    sys.exit(main())
