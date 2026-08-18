"""Shared helpers and configuration merging."""

import os
import tempfile
import unittest

from manimon import config, util


class Formatters(unittest.TestCase):
    def test_bytes_use_binary_units_at_every_step(self):
        for value, expected in ((0, '0B'), (512, '512B'), (1024, '1K'),
                                (1048576, '1M'), (1073741824, '1.0G'),
                                (1099511627776, '1.0T')):
            with self.subTest(value=value):
                self.assertEqual(util.fmt_bytes(value), expected)

    def test_rates_are_per_second_and_never_bare_numbers(self):
        self.assertEqual(util.fmt_rate(0), '0B/s')
        self.assertEqual(util.fmt_rate(2048), '2KB/s')
        self.assertTrue(util.fmt_rate(5 * 1024 ** 3).endswith('GB/s'))

    def test_elapsed_carries_the_next_unit_down_so_nothing_reads_as_bare(self):
        for value, expected in ((45, '45s'), (60, '1m00s'), (3599, '59m59s'),
                                (3600, '1h00m'), (86400, '1d00h')):
            with self.subTest(value=value):
                self.assertEqual(util.fmt_elapsed(value), expected)

    def test_age_is_coarser_than_elapsed_and_says_ago(self):
        self.assertEqual(util.fmt_age(30), '30s ago')
        self.assertEqual(util.fmt_age(3600), '60m ago')
        self.assertEqual(util.fmt_age(86400 * 3), '3d ago')


class WhichSearchesSbin(unittest.TestCase):
    """Privileged tools live in sbin, which is not on an ordinary user's PATH.
    Looking only at PATH reports them missing on the machines that have them."""

    def test_a_binary_on_path_is_found(self):
        self.assertIsNotNone(util.which('sh'))

    def test_a_binary_only_in_an_sbin_directory_is_still_found(self):
        with tempfile.TemporaryDirectory() as d:
            fake = os.path.join(d, 'notarealtool')
            open(fake, 'w').close()
            os.chmod(fake, 0o755)
            self.assertIsNone(util.which('notarealtool', extra_dirs=()))
            self.assertEqual(util.which('notarealtool', extra_dirs=(d,)), fake)

    def test_a_missing_binary_returns_none_rather_than_a_guessed_path(self):
        self.assertIsNone(util.which('definitely-not-installed-xyzzy'))


class ReadsNeverRaise(unittest.TestCase):
    """Every sensor read has to survive a file vanishing mid-scan; sysfs nodes
    come and go as devices are probed."""

    def test_a_missing_file_yields_the_default(self):
        self.assertEqual(util.rf('/nonexistent/path', 'fallback'), 'fallback')
        self.assertEqual(util.ri('/nonexistent/path', 7), 7)

    def test_unparseable_contents_yield_the_default_not_an_exception(self):
        with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as fh:
            fh.write('not a number')
            path = fh.name
        self.addCleanup(os.unlink, path)
        self.assertEqual(util.ri(path, -1), -1)


class ConfigMerge(unittest.TestCase):
    def test_a_partial_override_keeps_the_untouched_defaults(self):
        base = {'panels': {'width': 300, 'theme': 'light'}, 'limits': {'gpu': 85}}
        over = {'panels': {'width': 420}}
        got = config._deep_merge(base, over)
        self.assertEqual(got['panels']['width'], 420)
        self.assertEqual(got['panels']['theme'], 'light')
        self.assertEqual(got['limits']['gpu'], 85)

    def test_merging_does_not_mutate_the_defaults(self):
        base = {'panels': {'width': 300}}
        config._deep_merge(base, {'panels': {'width': 999}})
        self.assertEqual(base['panels']['width'], 300)

    def test_the_resolved_config_records_where_it_came_from(self):
        # An empty list means built-in defaults, which doctor reports as such.
        self.assertIn('_sources', config.CFG)
        self.assertIsInstance(config.CFG['_sources'], list)

    def test_every_documented_sample_key_exists_in_the_defaults(self):
        # The sample file is what users copy; a key in it that the loader does
        # not know would be silently ignored.
        for key in config.DEFAULTS:
            self.assertIn(key, config.CFG)


if __name__ == '__main__':
    unittest.main()
