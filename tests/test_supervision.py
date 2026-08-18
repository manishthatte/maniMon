"""
Panel supervision and the doctor checks that watch it.

Panels used to be spawned as children of a Type=oneshot watchdog and stayed in
its cgroup for their whole lives, which made systemd log four complaints every
time the 60-second timer fired — noise maniMon then read back out of the
journal and reported to the user as errors. manimon-panel@.service replaces
that, and `panels exec` is the piece that makes it correct: the panel must
REPLACE the process systemd is tracking, not be forked from it.

None of this needs GTK or a display; the exec is intercepted.
"""

import contextlib
import io
import pathlib
import unittest
from unittest import mock

from manimon.ui import launcher

ROOT = pathlib.Path(__file__).resolve().parent.parent
UNIT = ROOT / 'packaging' / 'systemd' / 'manimon-panel@.service'


class ExecPanel(unittest.TestCase):
    def test_it_execs_rather_than_forks(self):
        """The panel has to BE the service's main process.

        A fork would leave systemd tracking a shell that exits immediately,
        which is exactly the shape of the bug this replaced.
        """
        with mock.patch.object(launcher, '_display_ready', return_value=True), \
             mock.patch.object(launcher.os, 'execv') as execv:
            launcher.exec_panel('left')
        self.assertEqual(execv.call_count, 1)
        argv = execv.call_args[0][1]
        self.assertIn('manimon.ui.left', argv)
        self.assertIn('-m', argv)

    def test_each_side_execs_its_own_module(self):
        for side, mod in (('left', 'manimon.ui.left'), ('right', 'manimon.ui.right')):
            with mock.patch.object(launcher, '_display_ready', return_value=True), \
                 mock.patch.object(launcher.os, 'execv') as execv:
                launcher.exec_panel(side)
            self.assertIn(mod, execv.call_args[0][1])

    def test_no_display_exits_non_zero_without_execing(self):
        """Under Restart=always a non-zero exit is a retry, which is correct."""
        with mock.patch.object(launcher, '_display_ready', return_value=False), \
             mock.patch.object(launcher.os, 'execv') as execv, \
             contextlib.redirect_stderr(io.StringIO()):
            rc = launcher.exec_panel('left', wait=0)
        self.assertEqual(rc, 1)
        execv.assert_not_called()

    def test_both_is_rejected(self):
        """The unit passes %i, one side per instance. 'both' cannot be exec'd."""
        with mock.patch.object(launcher.os, 'execv') as execv, \
             contextlib.redirect_stderr(io.StringIO()):
            rc = launcher.exec_panel('both')
        self.assertEqual(rc, 2)
        execv.assert_not_called()

    def test_launch_routes_exec(self):
        with mock.patch.object(launcher, 'exec_panel', return_value=0) as ep:
            launcher.launch('exec', side='right')
        ep.assert_called_once_with('right')


def _directives(path):
    """The unit's actual settings, ignoring comments.

    Matching raw substrings would be wrong here: this unit's comments discuss
    the very directives being asserted about, so a naive `in` test passes and
    fails for the wrong reasons.
    """
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith(('#', ';', '[')) or '=' not in line:
            continue
        k, v = line.split('=', 1)
        out.setdefault(k.strip(), []).append(v.strip())
    return out


class PanelUnit(unittest.TestCase):
    """The unit file has to keep the properties the design depends on."""

    def setUp(self):
        self.d = _directives(UNIT)

    def test_it_is_a_template(self):
        self.assertTrue(UNIT.name.endswith('@.service'))
        self.assertTrue(any('%i' in v for vs in self.d.values() for v in vs))

    def test_it_restarts_the_panel(self):
        self.assertEqual(self.d.get('Restart'), ['always'])

    def test_it_stops_with_the_graphical_session(self):
        """Without PartOf, a logout leaves it retrying against a dead display."""
        self.assertIn('graphical-session.target', self.d.get('PartOf', []))

    def test_it_runs_the_exec_action(self):
        """Anything else would fork, and systemd would track the wrong process."""
        self.assertTrue(any('panels exec' in v for v in self.d.get('ExecStart', [])),
                        self.d.get('ExecStart'))

    def test_it_does_not_reintroduce_the_killmode_hack(self):
        """KillMode=process was only ever needed because panels shared a cgroup."""
        self.assertNotIn('KillMode', self.d)

    def test_the_watchdog_units_are_gone(self):
        d = ROOT / 'packaging' / 'systemd'
        for name in ('manimon-watchdog.service', 'manimon-watchdog.timer'):
            self.assertFalse((d / name).exists(), f'{name} is back')


class DoctorChecks(unittest.TestCase):
    def test_backup_log_check_warns_when_nothing_writes_the_path(self):
        """The original defect: a configured path no script updates."""
        from manimon import doctor
        r = doctor.Report()
        with mock.patch('manimon.config.BACKUP_DIR', '/nonexistent'), \
             mock.patch('manimon.config.BACKUP_JOBS',
                        [('nope.log', 'TEST_LABEL', 7)]):
            doctor.check_backup_logs(r)
        self.assertTrue(any(s == doctor.WARN and 'TEST_LABEL' in a
                            for s, a, _, _ in r.rows), r.rows)

    def test_reader_check_reports_a_failing_reader(self):
        from manimon import doctor

        class Snap:
            snap = {'_errors': {'gpus': 'sysfs went away'}}

            def tick(self, force_all=False):
                return self.snap

        r = doctor.Report()
        with mock.patch('manimon.collect.Collector', return_value=Snap()):
            doctor.check_readers(r)
        self.assertTrue(any(s == doctor.FAIL and a == 'reader:gpus'
                            for s, a, _, _ in r.rows), r.rows)

    def test_obsolete_unit_check_is_clean_here(self):
        from manimon import doctor
        r = doctor.Report()
        doctor.check_obsolete_units(r)
        self.assertTrue(r.rows)


if __name__ == '__main__':
    unittest.main()
