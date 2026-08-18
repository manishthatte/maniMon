"""
Progressive collapse: fitting the panel to the display it is actually on.

The left panel's content is 1585 px. That is 145 px past a 1440 px screen and
505 px past a 1080 px one, so on an ordinary laptop five sections would sit
below the fold on a fresh install. The panel now raises a density level and
sections shed detail in a published order until it fits.

Two things are worth guarding. The ladder must never hide something that is
trying to raise an alarm — a worn drive, a failing one, a backup disk that is
actually being written to. And it must not flap: the first attempt un-folded
whenever 40 px were spare, which oscillated between levels 1 and 2 once every
two seconds for as long as the panel ran, because level 2 fit with 44 px spare
and level 1 was 144 px taller.

GTK is not present on the CI runner, and by design nothing outside manimon/ui
needs it, so the parts that need a display skip there and run here.
"""

import contextlib
import io
import unittest

try:
    import os
    os.environ.setdefault('GDK_BACKEND', 'x11')
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk           # noqa: F401
    HAVE_GTK = Gtk.init_check([])[0]
except Exception:
    HAVE_GTK = False

needs_gtk = unittest.skipUnless(HAVE_GTK, 'no GTK / no display')


@needs_gtk
class FoldPredicates(unittest.TestCase):
    """What may be folded, and what may never be."""

    def setUp(self):
        from manimon.ui.sections import left_storage
        self.st = left_storage

    def test_a_healthy_unworn_drive_may_lose_its_smart_row(self):
        self.assertTrue(self.st._smart_is_quiet(
            {'life_pct': 100, 'healthy': True, 'bytes_written': 10}))

    def test_wear_is_never_folded_away(self):
        self.assertFalse(self.st._smart_is_quiet({'life_pct': 20, 'healthy': True}))

    def test_defects_are_never_folded_away(self):
        for k in ('reallocated', 'pending', 'media_errors'):
            self.assertFalse(self.st._smart_is_quiet(
                {'life_pct': 100, 'healthy': True, k: 1}), k)

    def test_a_failing_drive_is_never_folded_away(self):
        self.assertFalse(self.st._smart_is_quiet({'life_pct': 100, 'healthy': False}))

    def test_time_over_temperature_is_never_folded_away(self):
        self.assertFalse(self.st._smart_is_quiet(
            {'life_pct': 100, 'healthy': True, 'crit_temp_time': 3}))

    def test_an_idle_backup_drive_folds(self):
        dev = {'dev': 'sdc', 'usb': True}
        io = {'sdc': {'r_bps': 0, 'w_bps': 0}}
        smart = {'sdc': {'life_pct': 99, 'healthy': True}}
        self.assertTrue(self.st._is_idle(dev, io, smart))

    def test_a_drive_being_written_to_does_not_fold(self):
        """Mid-backup is exactly when that drive is worth a row."""
        dev = {'dev': 'sdc', 'usb': True}
        io = {'sdc': {'r_bps': 0, 'w_bps': 40 * 1024 * 1024}}
        self.assertFalse(self.st._is_idle(dev, io, {}))

    def test_an_unhealthy_backup_drive_does_not_fold(self):
        dev = {'dev': 'sdc', 'usb': True}
        io = {'sdc': {'r_bps': 0, 'w_bps': 0}}
        smart = {'sdc': {'life_pct': 100, 'healthy': False}}
        self.assertFalse(self.st._is_idle(dev, io, smart))

    def test_internal_drives_never_fold_as_idle(self):
        self.assertFalse(self.st._is_idle({'dev': 'nvme0n1', 'usb': False}, {}, {}))


@needs_gtk
class HeatGridReshape(unittest.TestCase):
    def test_reshape_changes_the_requested_height(self):
        from manimon.ui import widgets as W
        g = W.HeatGrid(8, 8, 21, list(range(64)))
        tall = g.get_size_request()[1]
        self.assertTrue(g.reshape(16, 4))
        self.assertLess(g.get_size_request()[1], tall)

    def test_reshaping_to_the_same_shape_is_a_no_op(self):
        from manimon.ui import widgets as W
        g = W.HeatGrid(8, 8, 21, list(range(64)))
        self.assertFalse(g.reshape(8, 8))


@needs_gtk
class FitLadder(unittest.TestCase):
    """The decision loop, driven by heights this test controls."""

    def _panel(self, heights, avail):
        from manimon.ui.window import PanelWindow

        class Fake(PanelWindow):
            def __init__(self):                    # no GTK window at all
                self.density = 0
                self._density_said = -1
                self._h_at = {}
                self._fit_avail = -1
                self._fit_probed = 0.0
                self.ANCHOR = 'TEST'
                self.renders = 0

            def _content_height(self):
                return heights[self.density]

            def _available_height(self):
                return avail

            def refresh(self, snap):
                self.renders += 1

        return Fake()

    @staticmethod
    def _fit(p, times=1):
        """Run the fit loop, swallowing the density-change diagnostic."""
        with contextlib.redirect_stdout(io.StringIO()):
            for _ in range(times):
                p._fit({})

    def test_it_stops_at_the_first_level_that_fits(self):
        p = self._panel({0: 1585, 1: 1531, 2: 1387, 3: 1351,
                         4: 1277, 5: 1212, 6: 1164}, avail=1431)
        self._fit(p)
        self.assertEqual(p.density, 2)

    def test_a_tall_enough_screen_folds_nothing(self):
        p = self._panel({0: 1585}, avail=2151)
        self._fit(p)
        self.assertEqual(p.density, 0)

    def test_it_bottoms_out_rather_than_looping_forever(self):
        heights = {d: 1600 for d in range(7)}       # nothing ever fits
        p = self._panel(heights, avail=800)
        self._fit(p)
        self.assertEqual(p.density, p.DENSITY_MAX)

    def test_it_does_not_oscillate(self):
        """The exact failure: level 2 fits with 44 px spare, level 1 is 144 taller.

        Slack-based un-folding flapped here once per tick forever. Deciding on
        the measured height of the level below cannot: after one visit, level 1
        is known not to fit.
        """
        heights = {0: 1585, 1: 1531, 2: 1387, 3: 1351,
                   4: 1277, 5: 1212, 6: 1164}
        p = self._panel(heights, avail=1431)
        self._fit(p)
        settled = p.density
        for _ in range(20):                          # twenty more ticks
            self._fit(p)
            self.assertEqual(p.density, settled)

    def test_it_gives_a_level_back_when_the_content_shrinks(self):
        heights = {0: 1585, 1: 1531, 2: 1387}
        p = self._panel(heights, avail=1431)
        self._fit(p)
        self.assertEqual(p.density, 2)
        heights[1] = 1400                            # a drive was unplugged
        p._h_at.clear()                              # what REPROBE does
        self._fit(p)
        self.assertEqual(p.density, 1)


if __name__ == '__main__':
    unittest.main()
