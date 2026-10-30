"""Explicit bounded resumes and the documentary repair, with receipts kept and no remote call."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from novel_engine.bounded_resume import POLICY, classify, public_metrics, stopped, transition
from novel_engine.campaign import Campaign
from novel_engine.contracts import ROOT, check_json
from novel_engine.instructions import WORLD_UNKNOWNS
from novel_engine.narrative import NarrativeLoop, packet_references
from novel_engine.recovery import RecoveryBudget
from novel_engine.scripts.repair_documentary_resume import repair, fingerprint, REPAIR_ID
import test_chronicle_constraints as chronicle
import test_documentary_loops as fixtures


class DocumentaryRepairTests(unittest.TestCase):
    tearDown = fixtures.DocumentaryFixture.tearDown
    memory = fixtures.DocumentaryFixture.memory

    def setUp(self):
        fixtures.DocumentaryFixture.setUp(self)
        self.rid = self.c.begin_action('What is the reputation of Maera?')['request_id']
        self.c.prepare_turn(narrative_memory=True)
        NarrativeLoop(self.c, self.n)._save_attempt(self.rid, 'required_context',
            {'packet': {'documentary_canon': {'facts': []}}, 'prompt': 'Old instructions.'}, reason='canon: Maera')
        with self.c.transaction() as con:
            RecoveryBudget(con, self.rid)
            con.execute('UPDATE action_recovery_budget SET generations=6,searches=6 WHERE request_id=?', (self.rid,))
            con.execute('INSERT OR REPLACE INTO memory_recovery VALUES (?,?,?,?,?,?)',
                        (self.rid, 'canon: old subject', 'to_clarify', 6, '{}', 'Old blockage.'))

    def state_and_limits(self):
        with self.c.transaction() as con:
            budget = RecoveryBudget(con, self.rid)
            return budget.state(), budget.limits()

    def test_preview_is_read_only_and_creates_no_backup(self):
        before = hashlib.sha256(self.c.path.read_bytes()).hexdigest()
        report = repair(self.c.path)
        self.assertEqual(report['status'], 'preview')
        self.assertEqual(report['limits'], {'generations': 8, 'searches': 8})
        self.assertEqual(hashlib.sha256(self.c.path.read_bytes()).hexdigest(), before)
        self.assertFalse((self.c.path.parent / 'saves').exists())
        self.assertEqual(self.n.calls, 0)

    def test_repair_preserves_game_receipts_and_cumulative_counts(self):
        before = self.c.resume()
        state = self.c.state()
        with self.c.transaction() as con:
            protected = fingerprint(con)
        report = repair(self.c.path, True)
        self.assertEqual(report['status'], 'prepared_resume')
        self.assertEqual(report['remaining'], {'generations': 2, 'searches': 1})
        self.assertEqual(self.c.resume(), before)
        self.assertEqual(self.c.state(), state)
        with self.c.transaction() as con:
            self.assertEqual(fingerprint(con), protected)
        counts, limits = self.state_and_limits()
        self.assertEqual(counts['generations'], 6)
        self.assertEqual(counts['searches'], 7)
        self.assertEqual(limits['generations'], 8)
        self.assertEqual(self.n.calls, 0)
        self.assertTrue(any(item.get('repair_id') == REPAIR_ID for item in counts['history']))

    def test_same_repair_is_idempotent_and_cannot_add_another_allowance(self):
        repair(self.c.path, True)
        before = self.state_and_limits()
        self.assertEqual(repair(self.c.path, True)['status'], 'already_applied')
        self.assertEqual(self.state_and_limits(), before)
        self.assertEqual(len(list((self.c.path.parent / 'saves').glob('*.sqlite'))), 1)

    def test_repaired_action_can_resume_explicitly_without_retyping(self):
        repair(self.c.path, True)
        result = NarrativeLoop(Campaign(self.c.path), self.n).run_turn(resume=True)
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(result['request_id'], self.rid)
        self.assertEqual(self.n.calls, 1)
        counts, _ = self.state_and_limits()
        self.assertEqual(counts['generations'], 7)

    def test_repair_still_stops_after_two_new_calls_and_cannot_be_replayed(self):
        repair(self.c.path, True)
        self.n.transform = lambda packet, response: self.memory('exchange:1')
        loop = NarrativeLoop(self.c, self.n)
        self.assertEqual(loop.run_turn(resume=True)['status'], 'memory_required')
        self.assertEqual(loop.run_turn(resume=True)['diagnostic_code'], 'recovery_blocked')
        self.assertEqual(self.n.calls, 2)
        repair(self.c.path, True)
        self.assertEqual(loop.run_turn(resume=True, retry=True)['diagnostic_code'], 'recovery_blocked')
        self.assertEqual(self.n.calls, 2)
        counts, _ = self.state_and_limits()
        self.assertEqual(counts['generations'], 8)

    def test_repair_is_not_available_without_a_new_context_or_dialogue_fix(self):
        preview = repair(self.c.path)
        with self.c.transaction() as con:
            original = json.loads(con.execute('SELECT proposal FROM narrative_attempts WHERE request_id=?',
                                              (self.rid,)).fetchone()[0])
            original.update(prompt='DIALOGUE AND LEARNING', packet={
                'documentary_canon': {'facts': [{'reference': ref} for ref in preview['new_references']]}})
            con.execute('UPDATE narrative_attempts SET proposal=? WHERE request_id=?', (check_json(original), self.rid))
        with self.assertRaisesRegex(ValueError, 'No new dialogue enrichment'):
            repair(self.c.path, True)
        self.assertFalse((self.c.path.parent / 'saves').exists())

    def test_a_changed_state_version_invalidates_the_allowance(self):
        repair(self.c.path, True)
        with self.c.transaction() as con:
            state = self.c._state(con)
            state['state_version'] += 1
            con.execute('UPDATE campaign SET state=?', (check_json(state),))
        _, limits = self.state_and_limits()
        self.assertEqual(limits['generations'], 2)
        self.assertEqual(repair(self.c.path, True)['status'], 'already_applied')

    def test_failure_rolls_back_the_technical_allowance(self):
        with patch('novel_engine.scripts.repair_documentary_resume.fingerprint', side_effect=['before', 'different']):
            with self.assertRaisesRegex(ValueError, 'protected data'):
                repair(self.c.path, True)
        counts, limits = self.state_and_limits()
        self.assertEqual(counts['generations'], 6)
        self.assertEqual(counts['searches'], 6)
        self.assertEqual(limits['generations'], 2)

    def test_repair_refuses_a_receipt_that_is_not_a_documentary_blockage(self):
        NarrativeLoop(self.c, self.n)._save_attempt(self.rid, 'failure', reason='Local validation refused.')
        with self.assertRaisesRegex(ValueError, 'documentary blockage'):
            repair(self.c.path, True)
        self.assertFalse((self.c.path.parent / 'saves').exists())

    def test_repair_refuses_a_budget_that_is_not_exhausted(self):
        with self.c.transaction() as con:
            con.execute('UPDATE action_recovery_budget SET generations=0,searches=0 WHERE request_id=?', (self.rid,))
        with self.assertRaisesRegex(ValueError, 'not exhausted'):
            repair(self.c.path, True)


class ResumeFixture(unittest.TestCase):
    """A blocked action whose budget is exhausted, as left by a refused documentary or memory step."""
    tearDown = fixtures.DocumentaryFixture.tearDown
    memory = fixtures.DocumentaryFixture.memory

    def setUp(self):
        fixtures.DocumentaryFixture.setUp(self)
        self.rid = self.c.begin_action('I ask for a clarification.')['request_id']
        self.prepared = self.c.prepare_turn(narrative_memory=True)
        self.query = 'Reread exchange:1.'
        self.c.recover_memory(self.rid, self.query)
        self.receipt = {'prepared': {'state': self.prepared['state']}, 'received': json.dumps(self.memory(self.query)),
                        'packet': {}, 'prompt': 'Old context.'}
        NarrativeLoop(self.c, self.n)._save_attempt(self.rid, 'required_context', self.receipt, reason=self.query)
        self.exhaust()

    def exhaust(self):
        with self.c.transaction() as con:
            RecoveryBudget(con, self.rid)
            con.execute('UPDATE action_recovery_budget SET generations=2,searches=2 WHERE request_id=?', (self.rid,))

    def counts(self):
        with self.c.transaction() as con:
            state = RecoveryBudget(con, self.rid).state()
        return state['generations'], state['searches']

    def grant(self, generations=1, searches=0, version=None):
        """What an explicit repair writes: one allowance bound to the current state version."""
        with self.c.transaction() as con:
            budget = RecoveryBudget(con, self.rid)
            state = budget.state()
            version = self.c._state(con)['state_version'] if version is None else version
            history = state['history'] + [{'repair_id': 'test_repair', 'state_version': version,
                'limits': {'generations': state['generations'] + generations, 'searches': state['searches'] + searches},
                'counts_before': {'generations': state['generations'], 'searches': state['searches']}}]
            con.execute('UPDATE action_recovery_budget SET history=? WHERE request_id=?',
                        (check_json(history), budget.request_id))

    def limits(self):
        with self.c.transaction() as con:
            return RecoveryBudget(con, self.rid).limits()


class ExplicitAllowanceTests(ResumeFixture):
    def test_exhausted_budget_blocks_every_resume_without_a_model_call(self):
        loop = NarrativeLoop(self.c, self.n)
        for retry in (False, True):
            result = loop.run_turn(resume=True, retry=retry)
            self.assertEqual(result['diagnostic_code'], 'recovery_blocked')
        self.assertEqual(self.n.calls, 0)
        self.assertEqual(self.counts(), (2, 2))

    def test_allowance_keeps_counters_and_adds_exactly_one_generation(self):
        before = self.c.resume()
        self.grant()
        self.assertEqual(self.c.resume(), before)
        self.assertEqual(self.counts(), (2, 2))
        self.assertEqual(self.limits(), {'generations': 3, 'searches': 2})
        with self.c.transaction() as con:
            self.assertEqual(tuple(con.execute('SELECT status FROM narrative_attempts').fetchone()), ('required_context',))

    def test_explicit_resume_can_complete_without_retyping(self):
        self.grant()
        result = NarrativeLoop(self.c, self.n).run_turn(resume=True)
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(result['request_id'], self.rid)
        self.assertEqual(self.n.calls, 1)
        self.assertEqual(self.counts(), (3, 2))

    def test_one_explicit_resume_then_no_further_calls_after_new_memory_request(self):
        self.grant()
        self.n.transform = lambda packet, response: self.memory(self.query)
        loop = NarrativeLoop(self.c, self.n)
        self.assertEqual(loop.run_turn(resume=True)['diagnostic_code'], 'recovery_blocked')
        self.assertEqual(loop.run_turn(resume=True, retry=True)['diagnostic_code'], 'recovery_blocked')
        self.assertEqual(self.n.calls, 1)

    def test_allowance_expires_if_state_changes_and_cannot_be_recharged(self):
        self.grant()
        with self.c.transaction() as con:
            state = self.c._state(con)
            state['state_version'] += 1
            con.execute('UPDATE campaign SET state=? WHERE id=1', (check_json(state),))
        self.assertEqual(self.limits(), {'generations': 2, 'searches': 2})
        self.assertEqual(NarrativeLoop(self.c, self.n).run_turn(resume=True)['diagnostic_code'], 'recovery_blocked')
        self.assertEqual(self.n.calls, 0)

    def test_allowance_written_for_another_version_is_ignored(self):
        self.grant(version=self.c.state()['state_version'] + 5)
        self.assertEqual(self.limits(), {'generations': 2, 'searches': 2})

    def test_two_allowances_never_lower_the_limit_and_the_highest_applies(self):
        self.grant(generations=1)
        self.grant(generations=3)
        self.assertEqual(self.limits()['generations'], 5)

    def test_both_prompt_modes_include_the_world_unknowns_correction(self):
        prepared = self.c.prepare_turn(narrative_memory=True)
        for compact in (True, False):
            with self.subTest(compact=compact):
                self.assertIn(WORLD_UNKNOWNS, NarrativeLoop(self.c, self.n).build_prompt(prepared, compact=compact))

    def test_requested_reference_comes_back_in_the_exact_prompt_after_allowance(self):
        self.grant()
        seen = []
        self.n.transform = lambda packet, response: seen.append(packet) or response
        NarrativeLoop(self.c, self.n).run_turn(resume=True)
        packet = seen[0]
        self.assertEqual(set(packet['allowed_references']), packet_references(packet))
        self.assertIn('exchange:1', packet['allowed_references'])


class PreparationDocumentTests(ResumeFixture):
    def setUp(self):
        super().setUp()
        self.preparation = self.folder / 'PREPARATION.md'
        self.relative = self.preparation.relative_to(ROOT).as_posix()
        self.old = 'The initial preparation leaves this point open.'
        self.new = 'Anchor explicitly confirmed: the fictional base is kept.'
        self.preparation.write_text(self.old, encoding='utf-8')
        with self.c.transaction() as con:
            config = json.loads(con.execute('SELECT config FROM campaign WHERE id=1').fetchone()[0])
            config['private_preparation'] = self.relative
            con.execute('UPDATE campaign SET config=? WHERE id=1', (check_json(config),))

    def documents(self, prompt):
        return json.loads(prompt.split('PREPARATION DOCUMENTS\n')[1].split('\nCURRENT TURN\n')[0])

    def test_a_corrected_preparation_reaches_both_prompts_and_the_resumed_turn(self):
        prepared = self.c.prepare_turn(narrative_memory=True)
        for compact in (True, False):
            texts = [d['text'] for d in self.documents(NarrativeLoop(self.c, self.n).build_prompt(prepared, compact=compact))
                     if d['source'] == self.relative]
            self.assertEqual(texts, [self.old])
        self.preparation.write_text(self.new, encoding='utf-8')
        for compact in (True, False):
            prompt = NarrativeLoop(self.c, self.n).build_prompt(prepared, compact=compact)
            texts = [d['text'] for d in self.documents(prompt) if d['source'] == self.relative]
            self.assertEqual(texts, [self.new])
        self.grant()
        loop = NarrativeLoop(self.c, self.n)
        result = loop.run_turn(resume=True)
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(self.n.calls, 1)

    def test_preparation_outside_the_project_is_refused(self):
        with self.c.transaction() as con:
            config = json.loads(con.execute('SELECT config FROM campaign WHERE id=1').fetchone()[0])
            config['private_preparation'] = '../outside.md'
            con.execute('UPDATE campaign SET config=? WHERE id=1', (check_json(config),))
        with self.assertRaisesRegex(ValueError, 'outside the project'):
            self.c.prepare_turn(narrative_memory=True)


class ChronicleResumeTests(unittest.TestCase):
    tearDown = chronicle.ChronicleConstraintsTests.tearDown
    report = chronicle.ChronicleConstraintsTests.report

    def setUp(self):
        chronicle.ChronicleConstraintsTests.setUp(self)
        self.report(1)
        self.report(2)
        self.rid = self.c.begin_action('I ask for a clarification.')['request_id']
        prepared = self.c.prepare_turn(narrative_memory=True)
        old_packet = {'world_agenda': json.loads(json.dumps(prepared['world_agenda']))}
        old_packet['world_agenda'].pop('chronicle_constraints')
        response = {'narration': 'Old refused answer.', 'events': [{'description': 'Fictional postponement.'}],
                    'memory_request': None, 'agenda_updates': [
                        {'entry_id': self.entry['entry_id'], 'status': 'postponed'}]}
        receipt = {'prepared': prepared, 'packet': old_packet, 'received': json.dumps(response),
                   'prompt': 'Old context without counters.'}
        NarrativeLoop(self.c, self.n)._save_attempt(self.rid, 'failure', receipt,
            reason='ValueError: postponement refused for ' + self.entry['entry_id'])
        with self.c.transaction() as con:
            budget = RecoveryBudget(con, self.rid)
            con.execute('UPDATE action_recovery_budget SET generations=2,searches=2 WHERE request_id=?', (self.rid,))
            history = budget.state()['history'] + [{'repair_id': 'test_repair',
                'state_version': self.c._state(con)['state_version'],
                'limits': {'generations': 3, 'searches': 2}}]
            con.execute('UPDATE action_recovery_budget SET history=? WHERE request_id=?',
                        (check_json(history), budget.request_id))

    def test_refused_receipt_is_kept_and_nothing_runs_without_an_explicit_call(self):
        with self.c.transaction() as con:
            before = tuple(con.execute('SELECT * FROM narrative_attempts').fetchone())
        self.assertEqual(self.n.calls, 0)
        with self.assertRaises(Exception):
            NarrativeLoop(self.c, self.n).run_turn(resume=True)
        with self.c.transaction() as con:
            self.assertEqual(tuple(con.execute('SELECT * FROM narrative_attempts').fetchone())[:3], before[:3])

    def test_explicit_retry_gets_the_new_context(self):
        def verify(packet, response):
            constraints = packet['world_agenda']['chronicle_constraints'][self.entry['entry_id']]
            self.assertFalse(constraints['postponement_allowed'])
            return response
        self.n.transform = verify
        result = NarrativeLoop(self.c, self.n).run_turn(resume=True, retry=True)
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(result['request_id'], self.rid)
        self.assertEqual(self.n.calls, 1)


class BoundedPolicyTests(unittest.TestCase):
    def test_public_metrics_keep_only_numbers_and_booleans_from_the_positive_list(self):
        metrics = {'model_calls': 2, 'prompt_bytes': 10, 'alert_budget': True, 'total_seconds': 1.5,
                   'prompt': 'secret text', 'reason': 'raw message', 'budget_ratio': 'high', 'planner': {'model_calls': 1}}
        self.assertEqual(public_metrics(metrics), {'model_calls': 2, 'prompt_bytes': 10, 'alert_budget': True,
                                                   'total_seconds': 1.5})

    def test_classification_distinguishes_phases_and_single_correction(self):
        class Narrator:
            last_error = {'category': 'timeout', 'process_stopped': True}
        self.assertEqual(classify(ValueError('x'), Narrator(), 'generation'), ('delay', True))
        Narrator.last_error = {'category': 'rate_limit'}
        self.assertEqual(classify(ValueError('x'), Narrator(), 'generation'), ('transient_service', False))
        Narrator.last_error = {'category': 'invalid_response'}
        self.assertEqual(classify(ValueError('x'), Narrator(), 'generation'), ('answer_contract', True))
        Narrator.last_error = {'category': 'auth'}
        self.assertEqual(classify(ValueError('x'), Narrator(), 'generation'), ('access', False))
        self.assertEqual(classify(ValueError('x'), Narrator(), 'application'), ('local_application', False))
        self.assertEqual(classify(ValueError('x'), Narrator(), 'preparation'), ('preparation_or_process', False))
        error = ValueError('x')
        error.__cause__ = KeyError('y')
        self.assertEqual(classify(error, Narrator(), 'validation'), ('answer_contract', True))
        self.assertEqual(classify(ValueError('x'), Narrator(), 'validation'), ('local_validation', False))


class StoppedDiagnosticTests(ResumeFixture):
    def test_transition_is_recorded_and_stop_exports_no_raw_content(self):
        value = transition(self.c, self.rid, 'search', 'sources_blocked', False)
        self.assertEqual((value['policy'], value['phase'], value['continue']), (POLICY, 'search', False))
        self.assertEqual((value['generations'], value['searches']), (2, 2))
        result = stopped(self.c, self.rid, 'sources_or_budget',
                         {'model_calls': 1, 'prompt': 'PRIVATE PROMPT', 'reason': 'RAW MESSAGE'}, 'search')
        self.assertEqual(result['status'], 'technical_stop')
        self.assertEqual(result['recovery_budget'], {'generations': 2, 'searches': 2})
        text = Path(result['diagnostic_path']).read_text(encoding='utf-8')
        for private in ('PRIVATE PROMPT', 'RAW MESSAGE', 'Reread exchange'):
            self.assertNotIn(private, text)
        self.assertEqual(json.loads(text)['measures'], {'model_calls': 1})
        with closing(sqlite3.connect(self.c.path)) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM recovery_transitions').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
