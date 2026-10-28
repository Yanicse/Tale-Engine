"""Pack-created campaigns support inventories and deterministic money transactions."""
import json
import tempfile
import unittest
from pathlib import Path

from novel_engine.campaign import Campaign
from novel_engine.demo import configuration, initial_state, event, proposal
from novel_engine.pack import campaign_config, seed_resources
from novel_engine.pack_check import check_pack
from test_possessions import item, op


class PackTransactionsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='.tmp-transactions-', dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def campaign(self, novel):
        (self.root / 'novel.json').write_text(json.dumps({'title': 'Test', **novel}), encoding='utf-8')
        self.assertEqual(check_pack(self.root).errors, [])
        config = campaign_config(self.root, configuration())
        state = initial_state()
        state['resources'] = seed_resources({}, config)
        return Campaign(self.root / 'campaign.sqlite').create(state, config)

    def apply(self, campaign, operations):
        state = campaign.state()
        e = event(state, 'The transaction completes.', [], causes=['current_action'])
        e['asset_operations'] = operations
        return campaign.apply(proposal(state, 'Done.', [e]))

    def test_currency_balances_purchase_and_insufficient_funds_are_atomic(self):
        c = self.campaign({'currencies': {'silver': {'initial_balance': 10}},
                           'currency_rates': {'gold': {'silver': 20}}})
        self.assertEqual(c.state()['resources']['player_gold'], 0)
        bought = item(); bought['references'] = ['current_action']
        result = self.apply(c, [op('purchase', -3, outputs={'wood': bought})])
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(c.state()['resources']['player_silver'], 7)
        before = c.state()
        self.assertEqual(self.apply(c, [op('payment', -8)])['status'], 'rejected')
        self.assertEqual(c.state(), before)
        result = self.apply(c, [op('sale', 2, inputs=[{'item_id': 'wood', 'quantity': 1}])])
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(c.state()['resources']['player_silver'], 9)
        self.assertNotIn('wood', c.state()['inventory'])

    def test_inventory_is_enabled_without_currency_rates(self):
        c = self.campaign({})
        acquired = item(); acquired['references'] = ['current_action']
        result = self.apply(c, [op('acquire', outputs={'wood': acquired})])
        self.assertEqual(result['status'], 'recorded', result)
        self.assertIn('wood', c.state()['inventory'])

    def test_reserved_resources_and_negative_balances_are_rejected(self):
        for novel in ({'currencies': {'hp': {'initial_balance': 1}}},
                      {'currencies': {'gold': {'initial_balance': -1}}},
                      {'currency_rates': {'gold': {'mana': 20}}}):
            (self.root / 'novel.json').write_text(json.dumps(novel), encoding='utf-8')
            self.assertTrue(check_pack(self.root).errors)

    def test_optional_character_sheet_does_not_disable_reserved_currency_checks(self):
        for unit in ('prevention_until_second', 'death_refusal_capacity_hp', 'prevention_survival_hp'):
            for novel in ({'currencies': {unit: {}}}, {'currency_rates': {unit: {'silver': 20}}},
                          {'currency_rates': {'silver': {unit: 20}}}):
                with self.subTest(unit=unit, novel=novel):
                    (self.root / 'novel.json').write_text(json.dumps(novel), encoding='utf-8')
                    self.assertTrue(check_pack(self.root).errors)
