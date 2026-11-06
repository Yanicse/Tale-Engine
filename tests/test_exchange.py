"""exchange: one currency becomes another at the rate of the pack, computed by the engine."""
import json
import tempfile
import unittest
from pathlib import Path

from novel_engine.campaign import Campaign
from novel_engine.demo import configuration, initial_state, event, proposal
from novel_engine.pack import campaign_config, seed_resources
from novel_engine.pack_check import check_pack
from test_possessions import item


def exchange(resource, delta, target):
    return {'kind': 'exchange', 'money_resource': resource, 'money_delta': delta, 'inputs': [], 'outputs': [],
            'destination': target}


class ExchangeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='.tmp-exchange-', dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        novel = {'title': 'Test', 'currencies': {'gold': {'initial_balance': 3}, 'silver': {'initial_balance': 5}},
                 'currency_rates': {'gold': {'silver': 20}, 'silver': {'gold': 0.05}}}
        (root / 'novel.json').write_text(json.dumps(novel), encoding='utf-8')
        self.assertEqual(check_pack(root).errors, [])
        config = campaign_config(root, configuration())
        state = initial_state()
        state['resources'] = seed_resources({}, config)
        self.c = Campaign(root / 'campaign.sqlite').create(state, config)

    def apply(self, operations, description='The exchange is made.'):
        state = self.c.state()
        e = event(state, description, [], causes=['current_action'])
        e['asset_operations'] = operations
        return self.c.apply(proposal(state, 'Done.', [e]))

    def balances(self):
        resources = self.c.state()['resources']
        return resources['player_gold'], resources['player_silver']

    def test_gold_becomes_silver_at_the_declared_rate(self):
        result = self.apply([exchange('player_gold', -2, 'player_silver')])
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(self.balances(), (1, 45))

    def test_the_way_back_exists_only_when_the_pack_declares_it(self):
        root = Path(self.temp.name) / 'oneway'
        root.mkdir()
        novel = {'title': 'Test', 'currencies': {'gold': {'initial_balance': 3}, 'silver': {'initial_balance': 40}},
                 'currency_rates': {'gold': {'silver': 20}}}
        (root / 'novel.json').write_text(json.dumps(novel), encoding='utf-8')
        config = campaign_config(root, configuration())
        state = initial_state()
        state['resources'] = seed_resources({}, config)
        other = Campaign(root / 'campaign.sqlite').create(state, config)
        e = event(other.state(), 'The way back.', [], causes=['current_action'])
        e['asset_operations'] = [exchange('player_silver', -20, 'player_gold')]
        result = other.apply(proposal(other.state(), 'Done.', [e]))
        self.assertEqual(result['status'], 'rejected')
        self.assertIn('No exchange rate', result['reason'])

    def test_silver_becomes_gold_only_in_whole_multiples(self):
        self.assertEqual(self.apply([exchange('player_silver', -5, 'player_gold')])['status'], 'rejected')
        self.assertEqual(self.apply([exchange('player_gold', -1, 'player_silver')])['status'], 'recorded')
        self.assertEqual(self.apply([exchange('player_silver', -25, 'player_gold')])['status'], 'rejected')
        result = self.apply([exchange('player_silver', -20, 'player_gold')])
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(self.balances(), (3, 5))

    def test_a_wrong_exchange_is_refused_with_its_reason_and_changes_nothing(self):
        before = self.c.state()
        cases = ((exchange('player_gold', -4, 'player_silver'), 'Insufficient balance'),
                 (exchange('player_gold', 1, 'player_silver'), 'Invalid currency or payment sign'),
                 (exchange('player_gold', -1, 'player_gold'), 'No exchange rate'),
                 (exchange('player_gold', -1, 'copper'), 'No exchange rate'),
                 (exchange('player_gold', -1, None), 'No exchange rate'))
        for operation, reason in cases:
            result = self.apply([operation])
            self.assertEqual(result['status'], 'rejected', operation)
            self.assertIn(reason, result['reason'])
        goods = item()
        goods['references'] = ['current_action']
        mixed = {**exchange('player_gold', -1, 'player_silver'), 'outputs': [{'item_id': 'x', 'item_json': json.dumps(goods)}]}
        result = self.apply([mixed])
        self.assertEqual(result['status'], 'rejected')
        self.assertIn('Exchange with objects', result['reason'])
        self.assertEqual(self.c.state(), before)

    def test_a_free_change_of_the_target_is_still_refused(self):
        state = self.c.state()
        e = event(state, 'The purse grows.', [{'operation': 'increment', 'path': '/resources/player_silver', 'value': 20}],
                  causes=['current_action'])
        result = self.c.apply(proposal(state, 'Done.', [e]))
        self.assertEqual(result['status'], 'rejected')
        self.assertIn('Monetary change without an explicit operation', result['reason'])

    def test_the_narration_check_is_not_confused_by_an_exchange(self):
        result = self.apply([exchange('player_gold', -1, 'player_silver')], 'The changer pays out twenty silver for one gold.')
        self.assertEqual(result['status'], 'recorded', result)


class ExchangeOnlyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='.tmp-exchange-only-', dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def campaign(self, novel):
        (self.root / 'novel.json').write_text(json.dumps({'title': 'Test', **novel}), encoding='utf-8')
        config = campaign_config(self.root, configuration())
        state = initial_state()
        state['resources'] = seed_resources({}, config)
        return Campaign(self.root / 'campaign.sqlite').create(state, config)

    def apply(self, campaign, operations):
        state = campaign.state()
        e = event(state, 'The window answers.', [], causes=['current_action'])
        e['asset_operations'] = operations
        return campaign.apply(proposal(state, 'Done.', [e]))

    def pack(self):
        return {'currencies': {'points': {'initial_balance': 5}, 'strength': {'initial_balance': 10, 'exchange_only': True}},
                'currency_rates': {'points': {'strength': 1}}}

    def test_a_stat_rises_only_by_spending_points(self):
        c = self.campaign(self.pack())
        self.assertEqual(self.apply(c, [exchange('player_points', -3, 'player_strength')])['status'], 'recorded')
        resources = c.state()['resources']
        self.assertEqual((resources['player_points'], resources['player_strength']), (2, 13))
        before = c.state()
        for delta in (4, -1):
            result = self.apply(c, [{**exchange('player_strength', delta, None), 'kind': 'payment'}])
            self.assertEqual(result['status'], 'rejected')
            self.assertIn('changes only through an exchange', result['reason'])
        self.assertEqual(self.apply(c, [exchange('player_points', -3, 'player_strength')])['status'], 'rejected')
        self.assertEqual(c.state(), before)

    def test_the_pack_check_explains_a_wrong_flag(self):
        for novel in ({'currencies': {'strength': {'exchange_only': 'yes'}}},
                      {'currencies': {'strength': {'exchange_only': True}}}):
            (self.root / 'novel.json').write_text(json.dumps({'title': 'Test', **novel}), encoding='utf-8')
            errors = ' '.join(check_pack(self.root).errors)
            self.assertIn('exchange_only', errors)
        (self.root / 'novel.json').write_text(json.dumps({'title': 'Test', **self.pack()}), encoding='utf-8')
        self.assertEqual(check_pack(self.root).errors, [])


if __name__ == '__main__':
    unittest.main()
