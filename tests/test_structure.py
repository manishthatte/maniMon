"""
Guards on the layout itself, so it cannot quietly rot back.

Everything here was a real defect before the 18 August 2026 restructure: three
copies of the same helpers, a superseded widget layer kept alive only by an
__all__ entry, aliases preserving compatibility with a history the public
repository never had, and sys.path manipulation in nine modules.
"""

import ast
import os
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / 'manimon'
MODULES = sorted(p for p in PKG.rglob('*.py') if '__pycache__' not in str(p))


def tree(path):
    return ast.parse(path.read_text())


class NoPathManipulation(unittest.TestCase):
    def test_no_module_edits_sys_path(self):
        """A real package does not need it, and nine modules used to do it."""
        offenders = [p.relative_to(ROOT).as_posix() for p in MODULES
                     if 'sys.path' in p.read_text()]
        self.assertEqual(offenders, [])


class NoDuplicatedHelpers(unittest.TestCase):
    HELPERS = {'rf', 'ri', 'sh', 'fmt_rate', 'fmt_bytes', 'fmt_elapsed', 'fmt_age'}

    def test_each_shared_helper_is_defined_exactly_once(self):
        seen = {}
        for p in MODULES:
            for node in tree(p).body:
                if isinstance(node, ast.FunctionDef) and node.name in self.HELPERS:
                    seen.setdefault(node.name, []).append(p.relative_to(ROOT).as_posix())
        for name, where in sorted(seen.items()):
            with self.subTest(helper=name):
                self.assertEqual(where, ['manimon/util.py'],
                                 f"{name} is defined in {where}")


class NoDeadExports(unittest.TestCase):
    def test_every_all_entry_actually_exists(self):
        """An __all__ naming a deleted symbol is how 84 lines of dead code
        stayed invisible to a reference scan for weeks."""
        for p in MODULES:
            t = tree(p)
            names = set()
            for node in ast.walk(t):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(node.name)
                elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                    names.add(node.id)
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    for a in node.names:
                        names.add((a.asname or a.name).split('.')[0])
            for node in t.body:
                if (isinstance(node, ast.Assign)
                        and any(getattr(x, 'id', '') == '__all__' for x in node.targets)):
                    for element in node.value.elts:
                        with self.subTest(module=p.name, export=element.value):
                            self.assertIn(element.value, names)


class NoPrivateOriginLeftovers(unittest.TestCase):
    BANNED = ('PHASE3', 'SIM_ROOTS', 'SIM_BINS', 'SIM_ID_RE')

    def test_no_aliases_from_the_tools_private_origin_survive(self):
        for p in MODULES:
            text = p.read_text()
            for name in self.BANNED:
                with self.subTest(module=p.name, name=name):
                    self.assertNotIn(name, text)


class ImportCost(unittest.TestCase):
    def test_the_cli_does_not_pull_in_gtk(self):
        """`manimon report` must work over SSH on a headless box. Subcommands
        import lazily; a stray top-level GTK import would break that and only
        show up on a machine without a display."""
        code = ("import sys; import manimon.cli; "
                "print('gi' in sys.modules or 'gi.repository' in sys.modules)")
        out = subprocess.run([sys.executable, '-c', code], cwd=ROOT,
                             capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), 'False', out.stderr)

    def test_the_collection_layer_does_not_pull_in_gtk(self):
        code = "import sys; import manimon.collect; print('gi' in sys.modules)"
        out = subprocess.run([sys.executable, '-c', code], cwd=ROOT,
                             capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), 'False', out.stderr)

    def test_the_ui_package_itself_stays_cheap(self):
        # Only the panel modules may touch GTK — importing manimon.ui to reach
        # the launcher must not require a display.
        code = "import sys; import manimon.ui; print('gi' in sys.modules)"
        out = subprocess.run([sys.executable, '-c', code], cwd=ROOT,
                             capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), 'False', out.stderr)


class ModuleSize(unittest.TestCase):
    LIMIT = 700

    def test_no_module_has_grown_into_a_god_file(self):
        """collectors.py reached 1,870 lines doing ten unrelated jobs."""
        big = {p.relative_to(ROOT).as_posix(): len(p.read_text().splitlines())
               for p in MODULES if len(p.read_text().splitlines()) > self.LIMIT}
        self.assertEqual(big, {})


class EveryModuleImports(unittest.TestCase):
    def test_the_whole_package_imports_without_a_display(self):
        mods = []
        for p in MODULES:
            rel = p.relative_to(ROOT).with_suffix('')
            name = '.'.join(rel.parts).replace('.__init__', '')
            if name.endswith('__main__'):
                continue
            mods.append(name)
        code = "import importlib\n" + "\n".join(f"importlib.import_module({m!r})"
                                                for m in mods)
        out = subprocess.run([sys.executable, '-c', code], cwd=ROOT,
                             capture_output=True, text=True,
                             env={**os.environ, 'DISPLAY': ''})
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])


class NamesResolve(unittest.TestCase):
    """Every name a UI function uses must actually be reachable.

    Two bugs of exactly this shape appeared during the 18 August 2026 split:
    a section calling `W.INK` in a module that no longer imported widgets, and
    a panel calling `p.L(...)` after a local had rebound `p` to a dict. Neither
    is caught by importing the module — only by executing the branch, which for
    a statistics line means having a populated store.
    """

    import builtins as _builtins
    BUILTINS = set(dir(_builtins))

    def _free_names(self, path):
        t = ast.parse(path.read_text())
        loads, bound = set(), set()
        for n in ast.walk(t):
            if isinstance(n, ast.Name):
                (loads if isinstance(n.ctx, ast.Load) else bound).add(n.id)
            elif isinstance(n, ast.arg):
                bound.add(n.arg)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(n.name)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    bound.add((a.asname or a.name).split('.')[0])
            elif isinstance(n, ast.comprehension):
                for x in ast.walk(n.target):
                    if isinstance(x, ast.Name):
                        bound.add(x.id)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                bound.add(n.name)
            elif isinstance(n, ast.alias):
                bound.add((n.asname or n.name).split('.')[0])
        return loads - bound - self.BUILTINS

    def test_no_ui_module_uses_a_name_it_cannot_reach(self):
        import importlib
        import manimon.ui.window as window
        star = set(getattr(window, '__all__', dir(window)))
        problems = {}
        for p in sorted(PKG.glob('ui/**/*.py')):
            if '__pycache__' in str(p):
                continue
            rel = p.relative_to(ROOT).with_suffix('')
            name = '.'.join(rel.parts).replace('.__init__', '')
            mod = importlib.import_module(name)
            missing = sorted(n for n in self._free_names(p)
                             if n not in star and not n.startswith('__')
                             and not hasattr(mod, n))
            if missing:
                problems[p.name] = missing
        self.assertEqual(problems, {})


class ResourcesAreClosed(unittest.TestCase):
    """
    Nothing may open a file or a database and walk away from it.

    The panels run for weeks and the readers are called on a ten-second tick,
    so a descriptor leaked per call is a descriptor leaked eight and a half
    thousand times a day. Two real ones lived here: `json.load(open(path))` in
    the job readers, and store.Reader holding a SQLite handle with no close()
    at all — the latter loud enough to raise ResourceWarning across the suite.
    """

    def test_every_open_is_managed(self):
        """open() must be a `with` item, or have its result assigned and closed.

        Bare `open(x).read()` and `json.load(open(x))` are the shapes that leak.
        """
        offenders = []
        for path in MODULES:
            tree = ast.parse(path.read_text())
            managed = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.With, ast.AsyncWith)):
                    for item in node.items:
                        managed.add(id(item.context_expr))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == 'open'
                        and id(node) not in managed):
                    offenders.append(f'{path.name}:{node.lineno}')
        self.assertEqual(offenders, [])

    def test_the_readers_can_be_closed(self):
        """Both readers must own their handle: close(), and the with-protocol."""
        from manimon.store.metrics import Reader
        from manimon.store.runs import RunReader
        for cls in (Reader, RunReader):
            for attr in ('close', '__enter__', '__exit__', '__del__'):
                self.assertTrue(callable(getattr(cls, attr, None)),
                                f'{cls.__name__} is missing {attr}')

    def test_closing_a_reader_twice_is_harmless(self):
        """__del__ runs after an explicit close(); it must not raise."""
        from manimon.store.metrics import Reader
        r = Reader()
        r.close()
        r.close()
        self.assertFalse(r.available)


if __name__ == '__main__':
    unittest.main()
