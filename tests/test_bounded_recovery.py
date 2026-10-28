"""Bounded automatic recovery: one correction at most, inside the action's budget, never a silent loop.

Ported from the source project's scenarios. The policy is optional: it only applies when the campaign
configuration says `recovery_policy: bounded_v1` (`novel configure --bounded-recovery on`).
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from novel_engine import campaign_tools
from novel_engine.adapters import ModelUnavailable
from novel_engine.agenda import Agenda
from novel_engine.bounded_resume import POLICY, public_metrics, stopped
from novel_engine.campaign import Campaign
from novel_engine.demo import configuration, initial_state
from novel_engine.narration_codex import CodexNarrator, normalize_response
from novel_engine.narrative import NarrativeLoop
from novel_engine.recovery import RecoveryBudget
from test_creations import Narrator
from test_planner import FakePlanner, install_test_chronicle


class Fixture(unittest.TestCase):
    def setUp(self):
        base = ROOT / '.tmp-tests'
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.c = Campaign(Path(self.temp.name) / 'campaign.sqlite').create(initial_state('bounded'), configuration())
        self.c.open_scene('An innkeeper waits at the door.')
        self.n = Narrator()

    def policy(self, on=True):
        campaign_tools.configure(self.c, apply=True, bounded_recovery=on)

    def transient_once(self, stopped_process=True):
        def fail_once(packet, answer):
            self.n.last_error = {'category': 'timeout', 'process_stopped': stopped_process}
            if self.n.calls == 1:
                raise ModelUnavailable('Timeout')
            return answer
        self.n.transform = fail_once


class BoundedRecoveryTests(Fixture):
    def test_without_the_policy_a_failure_stays_a_plain_error(self):
        self.transient_once()
        with self.assertRaises(ModelUnavailable):
            NarrativeLoop(self.c, self.n).run_turn('Hello')
        self.assertEqual(self.n.calls, 1)

    def test_a_memory_request_is_answered_locally_then_resolved_in_one_command(self):
        self.policy()
        self.n.transform = lambda p, r: ({**r, 'memory_request': {'query': 'innkeeper', 'reason': 'Recall the dialogue.'}}
                                         if self.n.calls == 1 else r)
        loop = NarrativeLoop(self.c, self.n)
        result = loop.run_turn('I ask for news.')
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(self.n.calls, 2)
        self.assertEqual(loop.last_metrics['automatic_recoveries'], 1)

    def test_a_confirmed_transient_failure_gets_exactly_one_new_generation(self):
        self.policy()
        self.transient_once(stopped_process=True)
        result = NarrativeLoop(self.c, self.n).run_turn('Hello')
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(self.n.calls, 2)

    def test_an_unconfirmed_process_stops_without_retry_and_a_cancel_is_not_retried(self):
        self.policy()
        self.transient_once(stopped_process=False)
        result = NarrativeLoop(self.c, self.n).run_turn('Hello')
        self.assertEqual(result['status'], 'technical_stop')
        self.assertEqual(self.n.calls, 1)

        def interrupted(packet, answer):
            raise KeyboardInterrupt()
        self.n.transform = interrupted
        with self.assertRaises(KeyboardInterrupt):
            NarrativeLoop(self.c, self.n).run_turn(resume=True, retry=True)
        self.assertEqual(self.n.calls, 2)
        self.n.transform = lambda p, r: r
        self.assertEqual(NarrativeLoop(self.c, self.n).run_turn(resume=True)['status'], 'technical_stop')
        self.assertEqual(self.n.calls, 2)

    def test_double_encoded_json_is_accepted_and_a_persistently_invalid_answer_stops_at_the_budget(self):
        answer = self.n.generate(NarrativeLoop(self.c, self.n).build_prompt(self.c.prepare_turn('Hello')))
        self.assertEqual(normalize_response(json.dumps(json.dumps(answer)))['narrative_schema_version'], '1.2')
        with self.assertRaises(ValueError):
            normalize_response('incomplete JSON')
        self.policy()
        self.n.transform = lambda p, r: {**r, 'events': [{}]}
        result = NarrativeLoop(self.c, self.n).run_turn(resume=True)
        self.assertEqual(result['status'], 'technical_stop')
        self.assertEqual(result['recovery_budget']['generations'], 2)

    def test_a_planning_call_before_the_narration_survives_the_retry(self):
        self.policy()
        with self.c.transaction() as con:
            install_test_chronicle(con)
        planner = FakePlanner()
        self.transient_once(stopped_process=True)
        loop = NarrativeLoop(self.c, self.n, planner=planner)
        self.assertEqual(loop.run_turn('Hello')['status'], 'recorded')
        self.assertEqual(planner.calls, 1)
        self.assertEqual(loop.last_metrics['planning_calls'], 1)
        with self.c.transaction() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM planner_reviews WHERE status='applied'").fetchone()[0], 1)

    def test_a_local_application_failure_is_diagnosed_and_the_receipt_reused(self):
        self.policy()
        from unittest.mock import patch
        before = self.c.state()
        with patch.object(self.c, 'apply', side_effect=OSError('Failure before commit')):
            result = NarrativeLoop(self.c, self.n).run_turn('Hello')
        self.assertEqual(result['diagnostic_code'], 'local_application')
        self.assertEqual(before, self.c.state())
        self.assertEqual(NarrativeLoop(self.c, self.n).run_turn(resume=True)['status'], 'recorded')
        self.assertEqual(self.n.calls, 1)

    def test_an_interrupted_goal_survives_the_automatic_memory_resolution(self):
        self.policy()
        with self.c.transaction() as con:
            agenda = Agenda(con)
            agenda.initialize()
            now = self.c._state(con)['world_minute']
            agenda.apply('interruption_fixture', [], {'goal': 'Travel', 'remaining': 'Arrive at the destination',
                                                      'status': 'suspended', 'event_index': 0}, [], now)
            before = Agenda(con).packet(self.c._state(con))['interrupted_goal']
        self.n.transform = lambda p, r: ({**r, 'memory_request': {'query': 'innkeeper', 'reason': 'Reminder'}}
                                         if self.n.calls == 1 else r)
        self.assertEqual(NarrativeLoop(self.c, self.n).run_turn('I ask for information.')['status'], 'recorded')
        with self.c.transaction() as con:
            self.assertEqual(Agenda(con).packet(self.c._state(con))['interrupted_goal'], before)

    def test_the_public_diagnostic_is_a_positive_list_without_secrets(self):
        request = self.c.begin_action('Hello')['request_id']
        result = stopped(self.c, request, 'local_application', {
            'cli_error': {'detail': 'Secret preparation; sk-fake-key'}, 'prompt': 'hidden NPC',
            'model_calls': 1, 'prompt_bytes': 20})
        text = Path(result['diagnostic_path']).read_text()
        for leaked in ('Secret', 'sk-fake', 'hidden'):
            self.assertNotIn(leaked, text)
        self.assertEqual(public_metrics({'model_calls': 'SECRET', 'prompt': 'secret'}), {})


class BudgetLineageTests(Fixture):
    def test_a_rejection_keeps_the_budget_and_an_identical_new_input_has_its_own(self):
        self.c.begin_action('Hello')
        proposal = self.c.prepare_turn()['model_proposal']
        proposal['expected_state_version'] = 99
        with self.c.transaction() as con:
            RecoveryBudget(con, proposal['request_id']).reserve_generation()
        self.assertEqual(self.c.apply(proposal)['status'], 'rejected')
        renewed = self.c.reevaluate_action()['request_id']
        with self.c.transaction() as con:
            budget = RecoveryBudget(con, renewed)
            self.assertEqual(budget.state()['generations'], 1)
            budget.reserve_generation()
            self.assertTrue(RecoveryBudget(con, renewed).generation_exhausted())
        blocked = NarrativeLoop(self.c, self.n).run_turn(resume=True)
        self.assertEqual(blocked['diagnostic_code'], 'recovery_blocked')
        # A receipt that is already valid stays applicable at the ceiling, then a new input has its own budget.
        proposal = self.c.prepare_turn()['model_proposal']
        proposal['narration'] = 'The conversation ends well.'
        self.assertEqual(self.c.apply(proposal)['status'], 'recorded')
        other = self.c.begin_action('Hello')['request_id']
        with self.c.transaction() as con:
            self.assertEqual(RecoveryBudget(con, other).state()['generations'], 0)


class ConfigurationTests(Fixture):
    def test_the_policy_never_changes_the_contract_version(self):
        for mechanics in (False, True):
            with self.c.transaction() as con:
                config = json.loads(con.execute('SELECT config FROM campaign').fetchone()[0])
                config['mechanics'] = {'version': 'mana-1.0'} if mechanics else None
                con.execute('UPDATE campaign SET config=?', (json.dumps(config),))
            for policy in (False, True):
                self.policy(policy)
                self.assertEqual(self.c.config().get('recovery_policy') == POLICY, policy)
                if not self.c.resume()['session']['pending_action']:
                    self.c.begin_action('Hello')
                prompt = NarrativeLoop(self.c, self.n).build_prompt(self.c.prepare_turn(narrative_memory=True))
                version = CodexNarrator(executable='fake').schema_for_prompt(prompt)['properties'][
                    'narrative_schema_version']['const']
                self.assertEqual(version, '1.3' if mechanics else '1.2')

    def test_activating_with_a_pending_receipt_keeps_it_and_deactivating_restores_the_default(self):
        from unittest.mock import patch
        loop = NarrativeLoop(self.c, self.n)
        with patch.object(self.c, 'apply', side_effect=OSError('Failure before commit')):
            with self.assertRaises(OSError):
                loop.run_turn('Hello')
        before, pending = self.c.state(), self.c.resume()['session']['pending_action']
        preview = campaign_tools.configure(self.c, bounded_recovery=True)
        self.assertFalse(preview['mutation'])
        self.assertEqual(self.c.state(), before)
        self.policy()
        self.assertEqual(self.c.resume()['session']['pending_action'], pending)
        self.assertEqual(NarrativeLoop(self.c, self.n).run_turn(resume=True)['status'], 'recorded')
        self.assertEqual(self.n.calls, 1)
        self.policy(False)
        self.assertIsNone(self.c.config()['recovery_policy'])


if __name__ == '__main__':
    unittest.main()
