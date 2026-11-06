"""A fixed cost in mana points: computed by the engine, independent of the maximum and of the power."""
import json
from pathlib import Path
import tempfile
import unittest

from novel_engine.abilities import packet
from novel_engine.campaign import Campaign
from novel_engine.demo import configuration, initial_state, event, proposal
from novel_engine.magic import cost_table, spell_cost
from novel_engine.pack import campaign_config, seed_resources, seed_powers
from novel_engine.pack_check import Report, check_sheet

RULES = {'abilities': {'dash': {'label': 'Dash', 'description': 'A short sprint.', 'limits': 'Brief.'},
                       'bolt': {'label': 'Bolt', 'description': 'A bolt.', 'limits': 'Short range.'}},
         'costs': {'default_output_percent': 100, 'full_power_cost_percent': {'bolt': 50},
                   'fixed_cost_points': {'dash': 5}}}


def sheet_problems(sheet):
    report = Report()
    check_sheet(sheet, report)
    return [str(problem) for problem in report.errors]


class FixedCostTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='.tmp-fixed-', dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.sheet = {'name': 'Ana', 'hp_max': 100, 'mana_max': 20, 'rules': RULES}
        (root / 'novel.json').write_text('{"title":"Test"}', encoding='utf-8')
        (root / 'character.json').write_text(json.dumps(self.sheet), encoding='utf-8')
        self.config = campaign_config(root, configuration())
        state = initial_state('fixed')
        state['resources'] = seed_resources(self.sheet, self.config)
        state['powers'] = seed_powers(self.sheet)
        self.c = Campaign(root / 'campaign.sqlite').create(state, self.config)

    def operation(self, kind, ability=None, amount=None, percent=None):
        return {'kind': kind, 'at_second': self.c.state()['world_minute'] * 60, 'ability_id': ability,
                'output_percent': percent, 'amount': amount, 'start_second': None, 'source_reference': 'current_action'}

    def apply(self, operations):
        state = self.c.state()
        e = event(state, 'The action completes.', [], causes=['current_action'])
        e['mechanical_operations'] = list(operations)
        return self.c.apply(proposal(state, 'Done.', [e]))

    def mana(self):
        return self.c.state()['resources']['player_mana']

    def test_the_engine_charges_exactly_the_fixed_points(self):
        self.assertEqual(self.apply([self.operation('ability', 'dash')])['status'], 'recorded')
        self.assertEqual(self.mana(), 15)
        self.assertEqual(self.apply([self.operation('ability', 'dash', percent=40)])['status'], 'recorded')
        self.assertEqual(self.mana(), 10)

    def test_the_cost_does_not_follow_a_larger_maximum(self):
        self.assertEqual(self.apply([self.operation('raise_max', 'mana', 548)])['status'], 'recorded')
        self.assertEqual(self.apply([self.operation('ability', 'dash')])['status'], 'recorded')
        self.assertEqual(self.mana(), 15)

    def test_a_percent_cost_still_follows_the_maximum(self):
        self.assertEqual(self.apply([self.operation('ability', 'bolt')])['status'], 'recorded')
        self.assertEqual(self.mana(), 10)

    def test_insufficient_mana_is_refused(self):
        for _ in range(4):
            self.assertEqual(self.apply([self.operation('ability', 'dash')])['status'], 'recorded')
        result = self.apply([self.operation('ability', 'dash')])
        self.assertEqual(result['status'], 'rejected')
        self.assertIn('Insufficient mana', result['reason'])
        self.assertEqual(self.mana(), 0)

    def test_the_views_show_the_fixed_cost(self):
        listed = {item['id']: item for item in packet(self.c.state(), self.config)['abilities']}
        self.assertEqual((listed['dash']['default_mana_cost'], listed['dash']['full_power_mana_cost']), (5, 5))
        self.assertEqual(spell_cost('dash', 548, self.config['spell_cost_rules'], 10), 5)
        table = cost_table(20, self.config['spell_cost_rules'])['mana_cost_per_power_pct']
        self.assertTrue(all(row['dash'] == 5 for row in table.values()))

    def test_the_sheet_check_accepts_and_refuses(self):
        self.assertEqual(sheet_problems(self.sheet), [])
        for fixed, text in (({'dash': 0}, 'positive whole number'), ({'dash': 2.5}, 'positive whole number'),
                            ({'ghost': 3}, 'no such ability'), ({'dash': 5, 'bolt': 5}, 'one cost per ability')):
            rules = json.loads(json.dumps(RULES))
            rules['costs']['fixed_cost_points'] = fixed
            problems = ' '.join(sheet_problems({**self.sheet, 'rules': rules}))
            self.assertIn(text, problems, fixed)

    def test_a_fixed_cost_alone_is_enough_and_a_missing_cost_is_not(self):
        rules = json.loads(json.dumps(RULES))
        del rules['costs']['full_power_cost_percent']
        rules['abilities'].pop('bolt')
        self.assertEqual(sheet_problems({**self.sheet, 'rules': rules}), [])
        del rules['costs']['fixed_cost_points']
        self.assertTrue(sheet_problems({**self.sheet, 'rules': rules}))


if __name__ == '__main__':
    unittest.main()
