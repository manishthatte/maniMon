"""
Per-run accounting.

The design rule under test: exclusive quantities belong to a run, shared ones
never get divided. One GPU feeding three jobs draws one board power, and
splitting it three ways invents a number no sensor measured.
"""

import os
import tempfile
import time
import unittest

from manimon.store import runs


class RunIdentity(unittest.TestCase):
    def test_pid_alone_is_not_the_identity(self):
        """Linux reuses PIDs; a long campaign wraps the space."""
        a = runs.run_key({'pid': 1234, 'start_ts': 1000.0})
        b = runs.run_key({'pid': 1234, 'start_ts': 9000.0})
        self.assertNotEqual(a, b)

    def test_the_key_is_stable_across_samples_of_the_same_run(self):
        # start_ts comes from the kernel, so it must not drift with the moment
        # of sampling — otherwise every tick would open a new "run".
        sim = {'pid': 42, 'start_ts': 1755500000.4}
        self.assertEqual(runs.run_key(sim), runs.run_key(dict(sim)))
        self.assertEqual(runs.run_key(sim), '42:1755500000')

    def test_a_snapshot_without_start_ts_degrades_instead_of_crashing(self):
        key = runs.run_key({'pid': 7, 'elapsed': 60})
        self.assertTrue(key.startswith('7:'))


class Concurrency(unittest.TestCase):
    """Peak concurrency is what gets reported instead of a fabricated share."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='manimon-runs-')
        self.path = os.path.join(self.dir, 'metrics.db')
        con = runs.metrics._connect(self.path)
        runs.metrics._schema(con)
        runs._schema(con)
        self.con = con
        self.reader = runs.RunReader(self.path)

    def tearDown(self):
        try:
            self.con.close()
        except Exception:
            pass
        for f in os.listdir(self.dir):
            os.unlink(os.path.join(self.dir, f))
        os.rmdir(self.dir)

    def _run(self, key, started, ended):
        self.con.execute(
            "INSERT OR REPLACE INTO runs (run_key, sim_id, pid, started, last_seen, ended) "
            "VALUES (?,?,?,?,?,?)", (key, 'job', 1, started, ended, ended))
        self.con.commit()

    def test_disjoint_runs_are_never_concurrent(self):
        self._run('a', 100, 200)
        self._run('b', 300, 400)
        self.assertEqual(self.reader.concurrent(100, 400), 1)

    def test_three_overlapping_runs_report_three(self):
        self._run('a', 100, 400)
        self._run('b', 150, 400)
        self._run('c', 200, 400)
        self.assertEqual(self.reader.concurrent(100, 400), 3)

    def test_a_brief_overlap_between_samples_is_still_caught(self):
        # Sweeping endpoints rather than sampling is the point: a 1-second
        # overlap inside a 10-second sampling interval must not be missed.
        self._run('a', 100, 201)
        self._run('b', 200, 300)
        self.assertEqual(self.reader.concurrent(100, 300), 2)

    def test_a_run_ending_exactly_as_another_starts_is_not_an_overlap(self):
        self._run('a', 100, 200)
        self._run('b', 200, 300)
        self.assertEqual(self.reader.concurrent(100, 300), 1)

    def test_an_empty_window_still_reports_at_least_one(self):
        self.assertEqual(self.reader.concurrent(0, 1), 1)


if __name__ == '__main__':
    unittest.main()
