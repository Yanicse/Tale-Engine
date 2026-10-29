"""Possession transactions, resumes, sources and CLI lookups without fictional progress."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from novel_engine.campaign import Campaign
from novel_engine.contracts import check_json
from novel_engine.demo import initial_state, configuration, event, proposal
from novel_engine.narration_codex import response_schema
from novel_engine.narrative import NarrativeLoop
from novel_engine.schema_codex import compile_schema
from novel_engine.scripts.play_narrative import main


def item(quantity=1, precision='exact', owner='player', status='owns', location='on:player'):
    return {'owner_id': owner, 'quantity': quantity, 'equipped': False, 'description': 'A lot of wood.',
            'references': ['demo:initial_configuration'], 'audience': ['narrator', 'player'],
            'location': location, 'condition': 'Sound', 'quantity_precision': precision,
            'possession_status': status, 'unit': 'piece'}


def op(kind, delta=0, inputs=None, outputs=None, destination=None):
    return {'kind': kind, 'money_resource': 'player_silver' if delta else None,
            'money_delta': delta, 'inputs': inputs or [],
            'outputs': [{'item_id': key, 'item_json': check_json(value)} for key, value in (outputs or {}).items()],
            'destination': destination}


class Narrator:
    def __init__(self, response):
        self.response, self.calls, self.last_metrics = response, 0, {}

    def generate(self, prompt):
        self.calls += 1
        self.prompt = prompt
        return deepcopy(self.response)


class PossessionTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / '.tmp-tests'
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        state, config = initial_state(), configuration()
        state['resources']['player_silver'] = 22
        state['inventory']['wood'] = item(5)
        config['possession_rules'] = {'version': 1, 'currencies': {
            'player_silver': {'owner_id': 'player', 'label': 'Silver coins'}}}
        config['resource_owners']['player_silver'] = 'player'
        self.c = Campaign(Path(self.temp.name) / 'campaign.sqlite').create(state, config)
        self.c.open_scene('The building site waits.')

    def tearDown(self):
        self.temp.cleanup()

    def apply(self, operations, description='Material operation accomplished.', narration='The goods are recorded.', changes=None):
        state = self.c.state()
        e = event(state, description, changes or [])
        e['asset_operations'] = operations
        p = proposal(state, narration, [e])
        return self.c.apply(p), p

    def rejected(self, operations, **kwargs):
        before = self.c.state()
        result, _ = self.apply(operations, **kwargs)
        self.assertEqual(result['status'], 'rejected', result)
        self.assertEqual(self.c.state(), before)
        self.assertEqual(result['narration'], '')
        return result

    def set_inventory(self, key, value):
        with self.c.transaction() as con:
            state = self.c._state(con)
            state['inventory'][key] = value
            con.execute('UPDATE campaign SET state=?', (check_json(state),))

    def test_purchase_is_atomic_and_replays_without_double_charge(self):
        result, p = self.apply([op('purchase', -3, outputs={'fittings': item(2)})])
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(self.c.state()['resources']['player_silver'], 19)
        self.assertEqual(self.c.state()['inventory']['fittings']['quantity'], 2)
        self.assertEqual(Campaign(self.c.path).apply(p), result)
        self.assertEqual(self.c.state()['resources']['player_silver'], 19)
        self.assertTrue(result['applied_events'][0]['changes'])

    def test_insufficient_funds_and_later_invalid_output_roll_back_everything(self):
        self.rejected([op('purchase', -23, outputs={'fittings': item(2)})])
        broken = item(2)
        broken.pop('location')
        self.rejected([op('purchase', -3, outputs={'fittings': broken})])
        self.rejected([op('purchase', -3, outputs={'fittings': item(0)})])

    def test_uncounted_quantity_is_reported_as_null_not_zero(self):
        result = self.rejected([op('acquire', outputs={'floats': item(None)})])
        self.assertIn('Null quantity (not counted) without quantity_precision=unknown: floats', result['reason'])
        self.assertNotIn('Zero', result['reason'])
        self.assertEqual(self.apply([op('acquire', outputs={'floats': item(None, 'unknown')})])[0]['status'], 'recorded')

    def test_sale_payment_and_service_expense(self):
        result, _ = self.apply([op('sale', 3, inputs=[{'item_id': 'wood', 'quantity': 2}]),
                                op('payment', -1), op('payment', 4)])
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(self.c.state()['resources']['player_silver'], 28)
        self.assertEqual(self.c.state()['inventory']['wood']['quantity'], 3)

    def test_craft_consumes_materials_and_records_products(self):
        result, _ = self.apply([op('craft', inputs=[{'item_id': 'wood', 'quantity': 3}], outputs={'pegs': item(8)})])
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(self.c.state()['inventory']['wood']['quantity'], 2)
        self.assertEqual(self.c.state()['inventory']['pegs']['quantity'], 8)

    def test_exhausted_lot_leaves_active_inventory(self):
        result, _ = self.apply([op('consume', inputs=[{'item_id': 'wood', 'quantity': 5}])])
        self.assertEqual(result['status'], 'recorded', result)
        self.assertNotIn('wood', self.c.state()['inventory'])

    def test_already_empty_lot_can_be_cleaned_but_full_lot_cannot_be_removed_silently(self):
        self.set_inventory('empty', item(0))
        self.rejected([], changes=[{'operation': 'remove', 'path': '/inventory/wood', 'value': None}])
        result, _ = self.apply([], changes=[{'operation': 'remove', 'path': '/inventory/empty', 'value': None}])
        self.assertEqual(result['status'], 'recorded', result)
        self.assertNotIn('empty', self.c.state()['inventory'])

    def test_stock_cannot_be_consumed_twice_in_one_turn(self):
        self.rejected([op('consume', inputs=[{'item_id': 'wood', 'quantity': 3}]),
                       op('consume', inputs=[{'item_id': 'wood', 'quantity': 3}])])

    def test_unknown_and_approximate_stock_require_counting_for_numeric_use(self):
        for precision, count in [('unknown', None), ('approximate', 5)]:
            self.set_inventory('wood', item(count, precision))
            self.rejected([op('consume', inputs=[{'item_id': 'wood', 'quantity': 1}])])

    def test_unknown_whole_lot_can_be_transformed_without_inventing_count(self):
        self.set_inventory('wood', item(None, 'unknown'))
        result, _ = self.apply([op('craft', inputs=[{'item_id': 'wood', 'quantity': None}],
                                  outputs={'pegs': item(None, 'unknown')})])
        self.assertEqual(result['status'], 'recorded', result)
        self.assertNotIn('wood', self.c.state()['inventory'])
        self.assertIsNone(self.c.state()['inventory']['pegs']['quantity'])

    def test_counting_unknown_stock_is_an_event_not_a_memory_requirement(self):
        self.set_inventory('wood', item(None, 'unknown'))
        result, _ = self.apply([], description='Mira counts the lot on site: five pieces.',
                               changes=[{'operation': 'set', 'path': '/inventory/wood', 'value': item(5)}])
        self.assertEqual(result['status'], 'recorded', result)

    def test_transfer_preserves_ownership_and_rejects_partial_lot(self):
        self.rejected([op('transfer', inputs=[{'item_id': 'wood', 'quantity': 2}], destination='site')])
        result, _ = self.apply([op('transfer', inputs=[{'item_id': 'wood', 'quantity': 5}], destination='site')])
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(self.c.state()['inventory']['wood']['location'], 'site')
        self.assertEqual(self.c.state()['inventory']['wood']['owner_id'], 'player')

    def test_transfer_unequips_without_rewriting_attributes_and_duplicate_is_refused(self):
        with self.c.transaction() as con:
            state = self.c._state(con)
            state['inventory']['wood']['equipped'] = True
            con.execute('UPDATE campaign SET state=?', (check_json(state),))
        original = self.c.state()['inventory']['wood']
        operation = op('transfer', inputs=[{'item_id': 'wood', 'quantity': 5}], destination='on:sailor')
        duplicate = dict(original, equipped=False)
        self.rejected([operation], changes=[{'operation': 'set', 'path': '/inventory/wood', 'value': duplicate}])
        result, _ = self.apply([operation])
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(self.c.state()['inventory']['wood'], dict(original, equipped=False, location='on:sailor'))

    def test_both_prompts_explain_transfer_computed_fields(self):
        from novel_engine.instructions import TRANSFERS
        prepared = self.c.prepare_turn('I entrust the lot to her.', narrative_memory=True)
        for compact in (True, False):
            self.assertIn(TRANSFERS, NarrativeLoop(self.c, None).build_prompt(prepared, compact=compact))

    def test_gift_rejects_invalid_or_unowned_lots_atomically(self):
        for destination in ('site', 'on:', 'on:npc:sailor'):
            self.rejected([op('gift', inputs=[{'item_id': 'wood', 'quantity': 5}], destination=destination)])
        self.rejected([op('gift', inputs=[{'item_id': 'wood', 'quantity': 2}], destination='on:sailor')])
        self.rejected([], changes=[{'operation': 'set', 'path': '/inventory/wood',
                                    'value': item(5, owner='sailor', location='on:sailor')}])
        for status, quantity in [('borrowed', 5), ('property_to_verify', 5), ('owns', 0)]:
            self.set_inventory('wood', item(quantity, status=status))
            self.rejected([op('gift', inputs=[{'item_id': 'wood', 'quantity': quantity or None}], destination='on:sailor')])

    def test_gift_gives_the_whole_new_owner_identifier(self):
        result, _ = self.apply([op('gift', inputs=[{'item_id': 'wood', 'quantity': 5}], destination='on:sailor')])
        self.assertEqual(result['status'], 'recorded', result)
        gifted = self.c.state()['inventory']['wood']
        self.assertEqual((gifted['owner_id'], gifted['location'], gifted['equipped']), ('sailor', 'on:sailor', False))

    def test_gift_preserves_unknown_quantity(self):
        self.set_inventory('wood', item(None, 'unknown'))
        result, _ = self.apply([op('gift', inputs=[{'item_id': 'wood', 'quantity': None}], destination='on:sailor')])
        self.assertEqual(result['status'], 'recorded', result)
        self.assertIsNone(self.c.state()['inventory']['wood']['quantity'])

    def test_borrowed_tool_cannot_be_sold_or_consumed(self):
        self.set_inventory('wood', item(5, owner='sailor', status='borrowed'))
        for operation in [op('sale', 3, inputs=[{'item_id': 'wood', 'quantity': 1}]),
                          op('consume', inputs=[{'item_id': 'wood', 'quantity': 1}])]:
            self.rejected([operation])

    def test_manual_currency_quantity_and_duplicate_operation_are_refused(self):
        self.rejected([], changes=[{'operation': 'increment', 'path': '/resources/player_silver', 'value': 3}])
        self.rejected([], changes=[{'operation': 'set', 'path': '/inventory/wood', 'value': item(2)}])
        self.rejected([op('payment', 3)], changes=[{'operation': 'increment', 'path': '/resources/player_silver', 'value': 3}])

    def test_existing_stock_is_never_overwritten_by_an_acquisition(self):
        self.rejected([op('acquire', outputs={'wood': item(50)})])

    def test_announced_payment_or_goods_without_saved_changes_is_refused(self):
        self.rejected([], description='Mira receives ten silver coins.')
        self.rejected([], description='Mira crafts pegs and brings back the materials.')
        self.rejected([], narration='You pay three silver coins.')
        self.rejected([], narration='You bring back the beams to the site.')

    def test_the_word_or_after_a_payment_verb_is_not_a_currency(self):
        # "or" is French for gold: the English conjunction must not announce a payment.
        result, _ = self.apply([], description='Mira pays or leaves; she decides to leave the stall.')
        self.assertEqual(result['status'], 'recorded', result)
        self.rejected([], description='Mira pays two gold coins to the carter.')

    def test_negotiation_and_unknown_information_do_not_require_mutations(self):
        result, _ = self.apply([], description='Mira receives information about the materials. No purchase made.',
                               narration='The seller offers three coins. You do not pay anything yet.')
        self.assertEqual(result['status'], 'recorded', result)

    def test_reporting_observations_and_receiving_a_quote_are_not_payments(self):
        for description, narration in [
                ('Mira reports her observations on the materials.', 'You report your observations on the wood.'),
                ('Mira receives a letter stating ten silver coins for the materials.', 'The quote is consulted.'),
                ('Mira receives a proposal about the materials.', 'You do not pay anything.'),
                ('Mira drinks a cup of water.', 'You consume water and regain strength.')]:
            result, _ = self.apply([], description=description, narration=narration)
            self.assertEqual(result['status'], 'recorded', result)

    def test_project_progress_is_durable_and_cannot_be_erased(self):
        project = {'title': 'House', 'owner_id': 'player', 'location': 'site', 'status': 'in_progress',
                   'plan': ['Four rooms'], 'completed': ['Layout drawn'], 'remaining': ['Frame'],
                   'agreements': [], 'unknowns': ['Dimensions to measure'], 'inventory_ids': ['wood'],
                   'references': ['demo:initial_configuration'], 'audience': ['narrator', 'player']}
        result, _ = self.apply([], changes=[{'operation': 'set', 'path': '/projects/house', 'value': project}])
        self.assertEqual(result['status'], 'recorded', result)
        changed = {**deepcopy(project), 'completed': ['Layout drawn', 'Supports installed']}
        result, _ = self.apply([], changes=[{'operation': 'set', 'path': '/projects/house', 'value': changed}])
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(Campaign(self.c.path).possessions()['projects']['house']['completed'], changed['completed'])
        self.rejected([], changes=[{'operation': 'remove', 'path': '/projects/house', 'value': None}])
        self.rejected([], changes=[{'operation': 'set', 'path': '/projects/house', 'value': project}])

    def test_currency_and_owner_are_checked_before_acquiring_goods(self):
        operation = op('purchase', -3, outputs={'fittings': item(2)})
        operation['money_resource'] = 'invented_currency'
        self.rejected([operation])
        self.rejected([op('purchase', -3, outputs={'fittings': item(2, owner='sailor')})])

    def test_agenda_conditions_see_program_calculated_purchases(self):
        from novel_engine.agenda import Agenda
        state = self.c.state()
        entry = {'entry_id': 'test:purchase', 'expected_revision': -1, 'title': 'Follow-up of the purchase',
                 'actor': 'player', 'location': 'site', 'kind': 'verification', 'due_minute': 1,
                 'conditions': [{'path': '/resources/player_silver', 'equals': 19}],
                 'references': ['demo:initial_configuration'], 'audience': ['narrator', 'player'],
                 'interrupt': False, 'status': 'planned', 'reason': 'Check the result.', 'outcome_event_index': None}
        with self.c.transaction() as con:
            agenda = Agenda(con)
            agenda.initialize()
            agenda._put(entry, 'test:preparation', 0)
        purchase = event(state, 'Mira buys the fittings.', [])
        purchase['asset_operations'] = [op('purchase', -3, outputs={'fittings': item(2)})]
        outcome = event(state, 'The result is checked.', [], minute=1, causes=['agenda:test:purchase'])
        update = {**deepcopy(entry), 'expected_revision': 0, 'status': 'finished', 'outcome_event_index': 1}
        result = self.c.apply(proposal(state, 'You buy the fittings, then finish the check.', [purchase, outcome]),
                              agenda_changes={'updates': [update], 'continuation': None, 'roll_checks': []})
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(self.c.state()['resources']['player_silver'], 19)

    def test_displayed_numeric_balance_must_match_saved_money(self):
        self.rejected([op('payment', 3)], narration='You have 30 silver coins.')

    def test_local_inventory_and_private_filter_call_no_model_and_play_no_turn(self):
        self.set_inventory('secret', {**item(), 'audience': ['narrator']})
        before = self.c.resume()
        out = io.StringIO()
        with patch('novel_engine.narration_codex.CodexNarrator') as model, redirect_stdout(out):
            self.assertEqual(main(['--campaign', str(self.c.path), '--inventory', '--json']), 0)
        model.assert_not_called()
        self.assertNotIn('secret', json.loads(out.getvalue())['possessions'])
        self.assertEqual(self.c.resume(), before)

    def test_schema_transport_and_aliases_support_an_actual_purchase(self):
        compile_schema(response_schema(version='1.2'))
        output = item(2)
        output['references'] = ['current_action']
        response = {'narration': 'You buy the fittings.', 'blocking_questions': [], 'events': [{
            'world_minute': 0, 'description': 'Mira buys the fittings.', 'causes': ['current_action'],
            'conditions': [], 'changes': [], 'audience': ['narrator', 'player'],
            'asset_operations': [op('purchase', -3, outputs={'fittings': output})]}]}
        narrator = Narrator(response)
        result = NarrativeLoop(self.c, narrator).run_turn('I spend three coins on the fittings.')
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(narrator.calls, 1)
        self.assertEqual(self.c.state()['resources']['player_silver'], 19)
        self.assertEqual(self.c.state()['inventory']['fittings']['references'], ['action:' + result['request_id']])

    def test_no_progress_memory_has_one_observable_fallback_then_stops_calls(self):
        response = {'narration': '', 'events': [], 'blocking_questions': [],
                    'memory_request': {'query': 'site', 'reason': 'Stock required.'},
                    'memory_updates': [], 'narrative_schema_version': '1.0'}
        narrator = Narrator(response)
        loop = NarrativeLoop(self.c, narrator)
        first = loop.run_turn('I check the site.')
        self.assertEqual(first['status'], 'memory_required')
        self.assertEqual(loop.run_turn(resume=True)['status'], 'memory_required')
        self.assertEqual(narrator.calls, 2)
        self.assertEqual(loop.run_turn(resume=True)['status'], 'memory_required')
        self.assertEqual(narrator.calls, 2)
        stopped = loop.run_turn(resume=True)
        self.assertEqual(narrator.calls, 2)
        self.assertEqual(stopped['status'], 'memory_required')
        self.assertEqual(stopped['diagnostic_code'], 'recovery_blocked')
        self.assertIsNotNone(self.c.resume()['session']['pending_action'])
        packet = json.loads(narrator.prompt.rsplit('\nCURRENT TURN\n', 1)[1])
        self.assertEqual(packet['memory_recovery']['status'], 'ready')


if __name__ == '__main__':
    unittest.main()
