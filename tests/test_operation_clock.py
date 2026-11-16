"""Dates of the mechanical operations: offsets and early timestamps are brought to the absolute clock."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from novel_engine.mechanics import MAX_STRETCH_SECONDS, _absolute_clock, effective_events


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


class EffectiveEventsTests(unittest.TestCase):
    CONFIG = {'mechanics': {'version': 'mana-1.0'}}
    STATE = {'world_minute': 300, 'world_second': 0}

    def test_without_installed_mechanics_the_events_are_returned_unchanged(self):
        events = [event(300, 0, [op(0), op(40)])]
        for config in ({}, {'mechanics': None}, {'mechanics': {'version': 'other'}}):
            with self.subTest(config=config):
                result = effective_events(self.STATE, events, config)
                self.assertEqual(result, events)
                self.assertEqual(result[0]['world_second'], 0)
                self.assertIsNot(result, events)

    def test_an_event_is_stretched_to_its_last_operation(self):
        events = [event(300, 0, [op(0, 'combat_start'), op(20), op(40, 'combat_end')])]
        result = effective_events(self.STATE, events, self.CONFIG)
        self.assertEqual((result[0]['world_minute'], result[0]['world_second']), (300, 40))
        self.assertEqual([o['at_second'] for o in result[0]['mechanical_operations']], [18000, 18020, 18040])
        self.assertEqual((events[0]['world_minute'], events[0]['world_second']), (300, 0))
        self.assertEqual([o['at_second'] for o in events[0]['mechanical_operations']], [0, 20, 40])

    def test_the_clock_is_carried_from_one_event_to_the_next(self):
        first = event(300, 0, [op(0), op(40)])
        second = event(301, 0, [op(0), op(10)])
        result = effective_events(self.STATE, [first, second], self.CONFIG)
        self.assertEqual((result[0]['world_minute'], result[0]['world_second']), (300, 40))
        self.assertEqual([o['at_second'] for o in result[1]['mechanical_operations']], [18040, 18050])
        self.assertEqual((result[1]['world_minute'], result[1]['world_second']), (301, 0))

    def test_an_event_without_operations_still_moves_the_clock(self):
        quiet = {'world_minute': 302, 'world_second': 0, 'mechanical_operations': []}
        later = event(303, 0, [op(0)])
        result = effective_events(self.STATE, [quiet, later], self.CONFIG)
        self.assertEqual(result[0], quiet)
        self.assertEqual(result[1]['mechanical_operations'][0]['at_second'], 302 * 60)

    def test_an_event_without_a_second_counts_from_the_whole_minute(self):
        state = {'world_minute': 300}
        result = effective_events(state, [{'world_minute': 300, 'mechanical_operations': [op(0), op(30)]}], self.CONFIG)
        self.assertEqual([o['at_second'] for o in result[0]['mechanical_operations']], [18000, 18030])
        self.assertEqual((result[0]['world_minute'], result[0]['world_second']), (300, 30))

    def test_a_stretch_beyond_the_limit_is_left_as_written(self):
        state = {'world_minute': 5000, 'world_second': 0}
        far = event(5010, 0, [op(5010 * 60 + MAX_STRETCH_SECONDS + 1)])
        result = effective_events(state, [far], self.CONFIG)
        self.assertIs(result[0], far)
        self.assertEqual((result[0]['world_minute'], result[0]['world_second']), (5010, 0))

    def test_an_operation_without_a_date_leaves_the_event_as_written(self):
        broken = event(300, 0, [{'kind': 'damage', 'amount': 6}])
        later = event(301, 0, [op(0)])
        result = effective_events(self.STATE, [broken, later], self.CONFIG)
        self.assertIs(result[0], broken)
        self.assertEqual(result[1]['mechanical_operations'][0]['at_second'], 300 * 60)

    def test_a_malformed_event_is_left_as_written_and_does_not_move_the_clock(self):
        state = {'world_minute': 300, 'world_second': 0}
        config = {'mechanics': {'version': 'mana-1.0'}}
        for broken in ({'mechanical_operations': [op(0)]}, {'world_minute': 300, 'mechanical_operations': ['x']},
                       {'description': 'No date.'}):
            with self.subTest(broken=broken):
                following = event(300, 0, [op(0), op(20)])
                result = effective_events(state, [broken, following], config)
                self.assertIs(result[0], broken)
                self.assertEqual((result[1]['world_minute'], result[1]['world_second']), (300, 20))

    def test_an_empty_list_of_events_gives_an_empty_list(self):
        self.assertEqual(effective_events(self.STATE, [], self.CONFIG), [])


if __name__ == '__main__':
    unittest.main()
