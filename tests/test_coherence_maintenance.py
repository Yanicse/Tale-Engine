"""Contradictions and maintenance interfaces, on fixtures only."""
from contextlib import redirect_stdout
from copy import deepcopy
import importlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from novel_engine.campaign import Campaign
from novel_engine.contracts import check_json
from novel_engine.demo import initial_state, configuration, event, proposal
from novel_engine.narrative_coherence import number
from novel_engine.narrative import NarrativeLoop


def item(quantity=1):
    return {'owner_id': 'player', 'quantity': quantity, 'equipped': False, 'description': 'A lot of wood.',
            'references': ['demo:initial_configuration'], 'audience': ['narrator', 'player'],
            'location': 'on:player', 'condition': 'Sound', 'quantity_precision': 'exact',
            'possession_status': 'owns', 'unit': 'piece'}


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
        return deepcopy(self.response)


class Fixture(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp-tests').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp-tests')
        self.folder = Path(self.temp.name)
        state, config = initial_state(), configuration()
        state['resources'].update(player_silver=22, player_hp=5000)
        state['inventory']['wood'] = item(5)
        config['possession_rules'] = {'version': 1, 'currencies': {
            'player_silver': {'owner_id': 'player', 'label': 'Silver coins'}}}
        config['resource_owners']['player_silver'] = 'player'
        self.c = Campaign(self.folder / 'fictional.sqlite').create(state, config)
        self.c.open_scene('The building site waits.')

    def tearDown(self):
        self.temp.cleanup()

    def submit(self, text, operations=(), claims=()):
        state = self.c.state()
        e = event(state, 'Fictional operation.', [])
        e['asset_operations'] = list(operations)
        e['public_consequences'] = list(claims)
        p = proposal(state, text, [e])
        return self.c.apply(p), p

    def reject(self, text, operations=(), claims=()):
        before = self.c.state()
        scene = self.c.resume()['session']['scene']
        result, _ = self.submit(text, operations, claims)
        self.assertEqual(result['status'], 'rejected', result)
        self.assertEqual(result['narration'], '')
        self.assertEqual(Campaign(self.c.path).state(), before)
        self.assertEqual(self.c.resume()['session']['scene'], scene)
        self.assertEqual(self.c.journal('player'), [])


class CoherenceTests(Fixture):
    def test_undeclared_payment_variants_and_wrong_direction(self):
        for text in ('You pay three coins.', 'You spend 3 coins.',
                     'You receive three coins.', 'You pay twenty-two coins.'):
            with self.subTest(text=text):
                self.reject(text)
        self.reject('You receive three coins.', [op('payment', -3)])
        self.reject('You pay four coins.', [op('payment', -3)])

    def test_true_payment_and_final_balance(self):
        result, _ = self.submit('You pay three coins. You have nineteen coins left.', [op('payment', -3)])
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(self.c.state()['resources']['player_silver'], 19)

    def test_final_balance_and_health_without_declared_claims(self):
        for text in ('You have twenty coins left.', 'Your purse contains 20 coins.',
                     'You have now twenty coins.', 'You have 4,900 HP.',
                     'Your hit points rise to 5,100.'):
            with self.subTest(text=text):
                self.reject(text)

    def test_quote_negation_hypothesis_and_recall_are_not_payments(self):
        for text in ('The merchant says: "You pay three coins."',
                     'You do not pay three coins.',
                     'If you pay three coins, he will accept.',
                     'You remember: yesterday, you pay three coins.',
                     'You could pay three coins.',
                     'You will pay three coins after the agreement.'):
            with self.subTest(text=text):
                result, _ = self.submit(text)
                self.assertEqual(result['status'], 'recorded', result)

    def test_state_link_uses_final_value_saved_and_replayed_exactly(self):
        text = 'You have [[state:/resources/player_silver]] coins.'
        result, p = self.submit(text, [op('payment', -3)])
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(result['narration'], 'You have 19 coins.')
        self.assertEqual(p['narration'], text)
        fresh = Campaign(self.c.path)
        self.assertEqual(fresh.apply(p), result)
        self.assertEqual(fresh.resume()['session']['scene'], result['narration'])
        self.assertEqual(fresh.state()['resources']['player_silver'], 19)

    def test_private_unknown_and_malformed_links_rollback(self):
        for path in ('/knowledge/faction', '/resources/faction_influence',
                     '/resources/player_unknown', '/inventory/wood'):
            with self.subTest(path=path):
                self.reject('Value: [[state:' + path + ']].', [op('payment', -3)])
        self.reject('Balance [[state:/resources/player_silver].')

    def test_acquisition_cannot_be_backed_by_another_item(self):
        claim = {'kind': 'acquisition', 'path': '/inventory/wood',
                 'value_json': json.dumps(item(5)), 'source_reference': 'demo:initial_configuration'}
        self.reject('An acquisition.', [op('purchase', -3, outputs={'nails': item(1)})], [claim])
        claim.update(path='/inventory/nails', value_json=json.dumps(item(1)))
        result, _ = self.submit('An acquisition.', [op('purchase', -3, outputs={'nails': item(1)})], [claim])
        self.assertEqual(result['status'], 'recorded', result)

    def test_payment_claim_requires_the_same_currency(self):
        with self.c.transaction() as con:
            state = self.c._state(con)
            config = json.loads(con.execute('SELECT config FROM campaign').fetchone()[0])
            state['resources']['player_copper'] = 8
            config['possession_rules']['currencies']['player_copper'] = {'owner_id': 'player', 'label': 'Coppers'}
            config['resource_owners']['player_copper'] = 'player'
            con.execute('UPDATE campaign SET state=?,config=?', (json.dumps(state), json.dumps(config)))
        claim = {'kind': 'payment', 'path': '/resources/player_copper', 'value_json': '8',
                 'source_reference': 'demo:initial_configuration'}
        self.reject('A payment.', [op('payment', -3)], [claim])

    def test_named_acquisition_cannot_be_hidden_by_an_unrelated_operation(self):
        self.reject('You receive the wood.', [op('acquire', outputs={'nails': item(1)})])
        result, _ = self.submit('You receive information about the wood.')
        self.assertEqual(result['status'], 'recorded', result)

    def test_number_forms(self):
        for text, value in [('twenty-two', 22), ('ninety-nine', 99), ('two hundred and three', 203),
                            ('five thousand', 5000), ('5' + chr(0x202f) + '000', 5000), ('4,900', 4900)]:
            self.assertEqual(number(text), value)

    def test_narrative_loop_renders_and_resumes_without_second_generation(self):
        narrator = Narrator({'narration': 'You have [[state:/resources/player_silver]] coins.',
                             'events': [], 'blocking_questions': []})
        result = NarrativeLoop(self.c, narrator).run_turn('I check my purse.')
        self.assertEqual(result['narration'], 'You have 22 coins.')
        replay = NarrativeLoop(Campaign(self.c.path), narrator).run_turn(resume=True)
        self.assertEqual(replay['narration'], result['narration'])
        self.assertEqual(narrator.calls, 1)

    def test_narrative_rejection_keeps_pending_action_and_scene(self):
        narrator = Narrator({'narration': 'You pay three coins.',
                             'events': [], 'blocking_questions': []})
        result = NarrativeLoop(self.c, narrator).run_turn('I pay three coins.')
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(result['narration'], '')
        session = Campaign(self.c.path).resume()['session']
        self.assertEqual(session['scene'], 'The building site waits.')
        self.assertEqual(session['pending_action']['text'], 'I pay three coins.')


class MaintenanceTests(Fixture):
    def clocks(self):
        path = self.folder / 'clocks.json'
        path.write_text('[]', encoding='utf-8')
        return ['--clocks', str(path)]

    def test_legacy_previews_are_read_only_and_never_call_mutation(self):
        before = self.c.path.read_bytes()
        files = set(self.folder.iterdir())
        for name, action, extra in [('migrate_memory', 'migrate', []), ('migrate_planner', 'migrate', []),
                                    ('install_chronicler', 'install', self.clocks())]:
            before_files = set(self.folder.iterdir())
            module = importlib.import_module('novel_engine.scripts.' + name)
            output = io.StringIO()
            with self.subTest(script=name), \
                    patch.object(module, action, side_effect=AssertionError('Mutation in preview')), \
                    redirect_stdout(output):
                module.main(['--campaign', str(self.c.path), *extra])
                self.assertEqual(json.loads(output.getvalue())['status'], 'preview')
            self.assertEqual(self.c.path.read_bytes(), before)
            # SQLite WAL may create its auxiliary files even in read-only mode.
            extra_files = set(self.folder.iterdir()) - files - before_files
            self.assertTrue(all(p.name in ('fictional.sqlite-wal', 'fictional.sqlite-shm') for p in extra_files))
            wal = self.c.path.with_name(self.c.path.name + '-wal')
            if wal.exists():
                self.assertEqual(wal.stat().st_size, 0)

    def test_no_argument_or_apply_without_target_is_refused_in_real_subprocesses(self):
        for name in ('migrate_memory', 'migrate_planner', 'install_chronicler', 'install_arcs',
                     'repair_documentary_resume'):
            for args in ([], ['--apply']):
                with self.subTest(script=name, args=args):
                    result = subprocess.run([sys.executable, '-B', str(ROOT / 'engine' / 'scripts' / f'{name}.py'), *args],
                                            cwd=self.folder, capture_output=True, timeout=30)
                    self.assertEqual(result.returncode, 2, result.stderr)

    def test_missing_target_does_not_create_database(self):
        from novel_engine.maintenance import execute
        from argparse import Namespace
        missing = self.folder / 'missing.sqlite'
        with self.assertRaises(ValueError):
            execute(Namespace(campaign=str(missing), apply=True), 'test', lambda c: self.fail('Mutation'))
        self.assertFalse(missing.exists())

    def test_apply_migration_on_fixture_preserves_scene_and_makes_backup(self):
        from novel_engine.scripts.migrate_memory import main
        before = self.c.state()
        session = self.c.resume()['session']
        with redirect_stdout(io.StringIO()):
            main(['--campaign', str(self.c.path), '--apply'])
        self.assertEqual(self.c.state(), before)
        self.assertEqual(self.c.resume()['session'], session)
        self.assertTrue(list((self.folder / 'saves').glob('*.sqlite')))

    def test_preview_does_not_write_report(self):
        report = self.folder / 'report.json'
        with redirect_stdout(io.StringIO()):
            importlib.import_module('novel_engine.scripts.migrate_planner').main(
                ['--campaign', str(self.c.path), '--report', str(report)])
        self.assertFalse(report.exists())

    def test_backup_before_write_is_unique_and_contains_previous_state(self):
        from novel_engine.maintenance import backup_before_write
        from novel_engine.narrative import narrative_lock
        before = self.c.state()
        paths = []
        for _ in range(2):
            with narrative_lock(self.c), backup_before_write(self.c, 'test') as path:
                paths.append(path)
                self.assertEqual(Campaign(path).state(), before)
        self.assertNotEqual(*paths)


if __name__ == '__main__':
    unittest.main()
