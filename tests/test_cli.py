"""
The command-line surface.

Argparse failures are the worst kind of silent: `manimon sensors --preflight`
was rejected from inside the root installer, where the exit status went
unchecked and the message scrolled past. It had been declared as a catch-all
positional, and argparse treats a leading `--` as an option no matter what a
positional claims to accept.

So every invocation this project's own scripts and documentation use is parsed
here. Parsing only — nothing is dispatched, so no sensor is read and no panel
is started.
"""

import argparse
import contextlib
import io
import pathlib
import re
import unittest

from manimon.cli import build_parser

ROOT = pathlib.Path(__file__).resolve().parent.parent


class Parsing(unittest.TestCase):
    def parse(self, *argv):
        return build_parser().parse_args(list(argv))

    def test_every_subcommand_is_reachable_with_no_arguments(self):
        sub = next(a for a in build_parser()._actions
                   if isinstance(a, argparse._SubParsersAction))
        for name in sub.choices:
            with self.subTest(command=name):
                a = self.parse(name)
                self.assertTrue(callable(getattr(a, 'fn', None)))

    def test_the_flags_the_installers_pass(self):
        for argv in (('sensors', '--preflight'),
                     ('sensors', '--once'),
                     ('sensors', '--once', '--print'),
                     ('sensors', '--show'),
                     ('sensors', '--once', '--force', 'ipmi'),
                     ('config', '--sample'),
                     ('report', '--hours', '48'),
                     ('runs', '--days', '30', '--active'),
                     ('runs', '--id', 'somejob'),
                     ('doctor', '-v'),
                     ('panels', 'ensure'),
                     ('panels', 'stop', '--side', 'left'),
                     ('dump', 'cpu', 'gpus')):
            with self.subTest(argv=argv):
                self.parse(*argv)          # must not raise SystemExit

    def test_panels_accepts_exactly_the_actions_the_launcher_implements(self):
        from manimon.ui import launcher
        sub = next(a for a in build_parser()._actions
                   if isinstance(a, argparse._SubParsersAction))
        action = next(a for a in sub.choices['panels']._actions
                      if a.dest == 'action')
        implemented = {'start', 'stop', 'status', 'restart', 'ensure'}
        self.assertEqual(set(action.choices), implemented)
        for name in implemented:
            self.assertTrue(callable(getattr(launcher, name, None)), name)

    def test_an_unknown_flag_is_rejected_rather_than_ignored(self):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                self.parse('sensors', '--not-a-real-flag')

    def test_version_is_the_packages_version(self):
        from manimon import __version__
        buf = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(buf):
            self.parse('--version')
        self.assertIn(__version__, buf.getvalue())


class DocumentedCommandsExist(unittest.TestCase):
    """Anything the README or an installer tells a user to type must parse."""

    def _invocations(self, text):
        # `manimon <cmd> ...` or `python3 -m manimon <cmd> ...`
        pat = re.compile(r'(?:python3? -m )?manimon ((?:[a-z-]+)(?: [^\n`|>&]*)?)')
        for m in pat.finditer(text):
            # Documented lines carry trailing `# what it does` comments; the
            # shell would not pass those either.
            argv = m.group(1).split('#')[0].split()
            argv = [a.strip('"\'') for a in argv if not a.startswith('$')]
            argv = [a for a in argv if a]
            if argv and argv[0].isalpha():
                yield argv

    def _check(self, path):
        parser = build_parser()
        sub = next(a for a in parser._actions
                   if isinstance(a, argparse._SubParsersAction))
        bad = []
        for argv in self._invocations(path.read_text()):
            if argv[0] not in sub.choices:
                continue                   # prose, not an invocation
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    parser.parse_args(argv)
            except SystemExit:
                bad.append(' '.join(argv))
        return bad

    def test_readme_invocations_parse(self):
        self.assertEqual(self._check(ROOT / 'README.md'), [])

    def test_installer_invocations_parse(self):
        for name in ('install_user_services.sh', 'install_system_sensors.sh'):
            with self.subTest(script=name):
                self.assertEqual(self._check(ROOT / 'packaging' / name), [])


if __name__ == '__main__':
    unittest.main()
