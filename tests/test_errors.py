"""
The failure channel.

A monitor whose reader is broken shows a default — an empty list, a zero, last
tick's value — and every one of those looks exactly like ordinary data. That is
the worst thing this program can do, so the path from "a reader raised" to
"something is on screen about it" is tested end to end rather than assumed.

The bug these guard against was real and shipped: Collector._try wrote every
exception into snap['_errors'] and nothing anywhere ever read that key.
"""

import unittest

from manimon.collect import Collector
from manimon.collect.attention import attention


def _collector_items(snap):
    return [i for i in attention(snap) if i['key'].startswith('collector:')]


class TryRecordsAndClears(unittest.TestCase):
    def setUp(self):
        self.c = Collector()
        self.c.snap.clear()

    def test_failure_is_recorded_with_the_default_substituted(self):
        self.c._try('gpus', lambda: 1 / 0, [])
        self.assertEqual(self.c.snap['gpus'], [])
        self.assertIn('gpus', self.c.snap['_errors'])

    def test_success_clears_a_previous_failure(self):
        """A transient error must not latch for the life of the process.

        self.snap persists across ticks, so an entry written once stays until
        something removes it. Before this, one blip meant a permanent alarm.
        """
        self.c._try('gpus', lambda: 1 / 0, [])
        self.assertIn('gpus', self.c.snap['_errors'])
        self.c._try('gpus', lambda: [{'card': 'card1'}], [])
        self.assertNotIn('gpus', self.c.snap['_errors'])
        self.assertEqual(self.c.snap['gpus'], [{'card': 'card1'}])

    def test_a_reader_that_is_not_wanted_is_not_run(self):
        c = Collector(want={'cpu'})
        c.snap.clear()
        c._try('gpus', lambda: 1 / 0, [])
        self.assertNotIn('gpus', c.snap.get('_errors', {}))


class FailuresReachTheAttentionPanel(unittest.TestCase):
    def test_a_failing_reader_becomes_a_visible_item(self):
        snap = {'_errors': {'gpu_metrics': 'sysfs went away'}}
        items = _collector_items(snap)
        self.assertEqual(len(items), 1)
        self.assertIn('gpu_metrics', items[0]['text'])
        self.assertIn('sysfs went away', items[0]['text'])

    def test_it_is_critical_and_cannot_be_acknowledged_away(self):
        """Dismissing it would restore exactly the silence being fixed."""
        item = _collector_items({'_errors': {'cpu': 'boom'}})[0]
        self.assertEqual(item['sev'], 0)            # SEV_CRIT
        self.assertFalse(item['ackable'])

    def test_no_errors_means_no_items(self):
        self.assertEqual(_collector_items({}), [])
        self.assertEqual(_collector_items({'_errors': {}}), [])

    def test_recorder_failures_use_the_same_channel(self):
        class Boom:
            def record(self, snap):
                raise RuntimeError('database is locked')

        c = Collector()
        c.recorder = Boom()
        c.tick()
        self.assertIn('recorder', c.snap.get('_errors', {}))
        self.assertTrue(_collector_items(c.snap))


if __name__ == '__main__':
    unittest.main()
