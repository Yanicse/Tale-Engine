"""Dates of the mechanical operations: offsets and early timestamps are brought to the absolute clock."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from novel_engine.mechanics import MAX_STRETCH_SECONDS, _absolute_clock


def op(at, kind='damage'):
    return {'kind': kind, 'at_second': at, 'ability_id': None, 'output_percent': None,
            'amount': 6, 'start_second': None, 'source_reference': 'current_action'}


def event(minute, second, operations):
    return {'world_minute': minute, 'world_second': second, 'mechanical_operations': operations}


class AbsoluteClockTests(unittest.TestCase):
    def test_relative_dates_are_shifted_and_event_stretched(self):
        state = {'world_minute': 300, 'world_second': 0}
        fixed = _absolute_clock(state, event(300, 0, [op(0, 'combat_start'), op(20), op(40, 'combat_end')]))
        self.assertEqual([o['at_second'] for o in fixed['mechanical_operations']], [18000, 18020, 18040])
        self.assertEqual((fixed['world_minute'], fixed['world_second']), (300, 40))

    def test_event_end_kept_when_it_already_covers_the_operations(self):
        state = {'world_minute': 300, 'world_second': 0}
        fixed = _absolute_clock(state, event(302, 0, [op(0), op(20)]))
        self.assertEqual((fixed['world_minute'], fixed['world_second']), (302, 0))
        self.assertEqual([o['at_second'] for o in fixed['mechanical_operations']], [18000, 18020])

    def test_absolute_dates_inside_the_event_are_never_touched(self):
        state = {'world_minute': 300, 'world_second': 0}
        original = event(301, 0, [op(18010), op(18030)])
        self.assertIs(_absolute_clock(state, original), original)

    def test_recent_absolute_date_is_not_mistaken_for_an_offset(self):
        state = {'world_minute': 40979, 'world_second': 50}
        original = event(40979, 58, [op(2458758)])
        fixed = _absolute_clock(state, original)
        self.assertEqual(fixed['mechanical_operations'][0]['at_second'], 2458790)
        self.assertEqual((fixed['world_minute'], fixed['world_second']), (40979, 58))
        self.assertEqual(original['mechanical_operations'][0]['at_second'], 2458758)

    def test_nearby_early_operation_keeps_following_absolute_dates(self):
        state = {'world_minute': 300, 'world_second': 5}
        fixed = _absolute_clock(state, event(300, 30, [op(17999), op(18020)]))
        self.assertEqual([o['at_second'] for o in fixed['mechanical_operations']], [18005, 18020])
        self.assertEqual((fixed['world_minute'], fixed['world_second']), (300, 30))

    def test_absolute_dates_after_the_event_end_stretch_the_event(self):
        state = {'world_minute': 1930, 'world_second': 0}
        fixed = _absolute_clock(state, event(1935, 0, [op(116100, 'combat_start'), op(116130), op(116280, 'combat_end')]))
        self.assertEqual((fixed['world_minute'], fixed['world_second']), (1938, 0))
        self.assertEqual([o['at_second'] for o in fixed['mechanical_operations']], [116100, 116130, 116280])

    def test_unordered_absolute_dates_are_not_repaired(self):
        state = {'world_minute': 300, 'world_second': 0}
        original = event(300, 0, [op(18040), op(18020)])
        self.assertIs(_absolute_clock(state, original), original)

    def test_mixed_dates_are_not_guessed(self):
        state = {'world_minute': 300, 'world_second': 0}
        original = event(301, 0, [op(20), op(18030)])
        self.assertIs(_absolute_clock(state, original), original)

    def test_stale_absolute_date_is_not_read_as_an_offset(self):
        state = {'world_minute': 5000, 'world_second': 0}
        original = event(5010, 0, [op(299900)])
        self.assertIs(_absolute_clock(state, original), original)

    def test_date_far_after_the_event_does_not_stretch_it(self):
        state = {'world_minute': 5000, 'world_second': 0}
        original = event(5010, 0, [op(5010 * 60 + MAX_STRETCH_SECONDS + 1)])
        self.assertIs(_absolute_clock(state, original), original)
        fixed = _absolute_clock(state, event(5010, 0, [op(5010 * 60 + MAX_STRETCH_SECONDS)]))
        self.assertEqual((fixed['world_minute'], fixed['world_second']), (5070, 0))

    def test_offsets_far_beyond_the_event_are_not_guessed(self):
        state = {'world_minute': 5000, 'world_second': 0}
        original = event(5010, 0, [op(0), op(600 + MAX_STRETCH_SECONDS + 1)])
        self.assertIs(_absolute_clock(state, original), original)
        fixed = _absolute_clock(state, event(5010, 0, [op(0), op(900)]))
        self.assertEqual([o['at_second'] for o in fixed['mechanical_operations']], [300000, 300900])
        self.assertEqual((fixed['world_minute'], fixed['world_second']), (5015, 0))

    def test_clock_at_zero_changes_nothing(self):
        state = {'world_minute': 0, 'world_second': 0}
        original = event(1, 0, [op(0), op(20)])
        self.assertIs(_absolute_clock(state, original), original)


if __name__ == '__main__':
    unittest.main()
