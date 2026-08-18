"""
The metric store.

The first test here is the one that matters most: the recorder once ran an
entire session writing precisely nothing, because sqlite3 binds a connection to
the thread that created it and the panel built its recorder on the GTK main
thread while feeding it from a worker. Every insert raised, and the error was
caught into a field nobody read.
"""

import os
import sqlite3
import tempfile
import threading
import time
import unittest

from manimon.store import metrics


def snapshot(cpu=50.0, gpu_temp=60.0, gpu_power=100.0, ts=None):
    """The subset of a real snapshot that row_from_snapshot actually reads."""
    return {
        'ts': ts or time.time(),
        'cpu': {'pct': cpu, 'temp': 55.0, 'power': 120.0, 'freq': 3000},
        'gpus': [{'busy': 80, 'temp_junction': gpu_temp, 'temp_mem': 70,
                  'power': gpu_power, 'vram_used_pct': 40}],
        'mem': {'used_pct': 30.0, 'swap_used_pct': 0.0},
    }


class TempStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='manimon-test-')
        self.path = os.path.join(self.dir, 'metrics.db')

    def tearDown(self):
        for f in os.listdir(self.dir):
            os.unlink(os.path.join(self.dir, f))
        os.rmdir(self.dir)


class RecorderThreading(TempStore):
    def test_records_from_a_thread_other_than_the_one_that_built_it(self):
        """The original bug, as a test.

        Construct on this thread, write from another. Without
        check_same_thread=False this raises ProgrammingError inside record(),
        which swallows it — so the assertion is on the ROW COUNT, not on the
        absence of an exception. That distinction is the whole lesson: the old
        code did not crash, it just silently stored nothing.
        """
        rec = metrics.Recorder(self.path)
        self.addCleanup(rec.close)

        written = []

        def worker():
            written.append(rec.record(snapshot(), now=1000.0))

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        self.assertEqual(written, [True], f"record() failed: {rec.last_error}")
        con = sqlite3.connect(self.path)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 1)
        con.close()
        self.assertIsNone(rec.last_error)

    def test_concurrent_writers_do_not_corrupt_the_store(self):
        rec = metrics.Recorder(self.path)
        self.addCleanup(rec.close)
        errors = []

        def worker(base):
            for i in range(20):
                try:
                    rec.record(snapshot(), now=base + i * metrics.RAW_EVERY)
                except Exception as e:          # must never escape record()
                    errors.append(e)

        threads = [threading.Thread(target=worker, args=(2000.0 + n * 1000,))
                   for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertIsNone(rec.last_error)


class RateLimit(TempStore):
    def test_a_second_sample_inside_the_interval_is_skipped(self):
        rec = metrics.Recorder(self.path)
        self.addCleanup(rec.close)
        self.assertTrue(rec.record(snapshot(), now=1000.0))
        self.assertFalse(rec.record(snapshot(), now=1000.0 + metrics.RAW_EVERY / 2))
        self.assertTrue(rec.record(snapshot(), now=1000.0 + metrics.RAW_EVERY))


class Folding(TempStore):
    """Folding must not average a thermal peak away — that would defeat the
    entire point of keeping history."""

    def _fill(self, con, res, base, values, column='gpu_temp_junction'):
        for i, v in enumerate(values):
            con.execute(f"INSERT OR REPLACE INTO samples (res, ts, {column}) VALUES (?,?,?)",
                        (res, int(base + i * 10), v))
        con.commit()

    def test_temperature_folds_to_the_maximum_and_rates_to_the_mean(self):
        con = metrics._connect(self.path)
        self.addCleanup(con.close)
        metrics._schema(con)

        now = 2_000_000
        old = now - metrics.RETENTION['r'] - 3600      # safely past raw retention
        base = old - (old % 60)                        # align to a minute bucket
        self._fill(con, 'r', base, [40.0, 90.0, 41.0, 42.0, 43.0, 44.0])
        for i in range(6):
            con.execute("UPDATE samples SET cpu_pct=? WHERE res='r' AND ts=?",
                        (10.0 * (i + 1), int(base + i * 10)))
        con.commit()

        metrics.fold(con, now=now)

        row = con.execute("SELECT gpu_temp_junction, cpu_pct FROM samples "
                          "WHERE res='1m' ORDER BY ts").fetchone()
        self.assertIsNotNone(row, "nothing was folded")
        peak, mean = row
        self.assertEqual(peak, 90.0, "the peak was averaged away")
        self.assertAlmostEqual(mean, 35.0, places=3)   # mean of 10..60

    def test_folding_twice_changes_nothing(self):
        con = metrics._connect(self.path)
        self.addCleanup(con.close)
        metrics._schema(con)
        now = 2_000_000
        base = now - metrics.RETENTION['r'] - 3600
        self._fill(con, 'r', base - (base % 60), [50.0] * 6)

        metrics.fold(con, now=now)
        first = con.execute("SELECT res, ts, gpu_temp_junction FROM samples "
                            "ORDER BY res, ts").fetchall()
        metrics.fold(con, now=now)
        second = con.execute("SELECT res, ts, gpu_temp_junction FROM samples "
                             "ORDER BY res, ts").fetchall()
        self.assertEqual(first, second)


class SchemaMigration(TempStore):
    def test_a_store_missing_a_column_gains_it_without_losing_rows(self):
        con = metrics._connect(self.path)
        metrics._schema(con)
        con.execute("INSERT INTO samples (res, ts, cpu_pct) VALUES ('r', 1, 42.0)")
        con.commit()
        con.close()

        # Re-opening must migrate in place, not start over.
        con = metrics._connect(self.path)
        self.addCleanup(con.close)
        metrics._schema(con)
        rows = con.execute("SELECT cpu_pct FROM samples WHERE ts=1").fetchall()
        self.assertEqual(rows, [(42.0,)])
        cols = {r[1] for r in con.execute("PRAGMA table_info(samples)")}
        for expected in metrics.COLUMNS:
            self.assertIn(expected, cols)


if __name__ == '__main__':
    unittest.main()
