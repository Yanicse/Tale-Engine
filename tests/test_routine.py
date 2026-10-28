"""Ellipses on fictional campaigns: bounds, resume, confidentiality and validations."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from novel_engine.agenda import Agenda
from novel_engine.arcs import ArcRegistry
from novel_engine.campaign import Campaign
from novel_engine.demo import initial_state, configuration, event, proposal
from novel_engine.planner import WorldPlanner, long_wait
from novel_engine.routine import (Routine, preview, turn_limit, read_json, write_json, NoRemote,
                         LEGACY_POLICY, daily_points, normalize_policy)


class RoutineTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / '.tmp-tests'
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.temp.name)
        state = initial_state()
        state['world_minute'] = 1080
        self.c = Campaign(self.root / 'novel_engine.sqlite').create(state, configuration())
        self.c.open_scene('The guard waits.')
        self.r = Routine.create(self.root / 'routine', self.c, days=2,
                                objective="Follow the world's news.", policy=LEGACY_POLICY)

    def tearDown(self):
        self.temp.cleanup()

    def work(self):
        return read_json(Path(self.r.prepare()['step']) / 'work.json')

    def response(self, work=None, minute=None):
        w = work or self.work()
        return {'narrative_schema_version': '1.2', 'narration': 'The day passes; no new mail.',
                'events': [{'world_minute': w['bound'] if minute is None else minute,
                    'description': 'The day passes with no news.', 'causes': ['current_action'],
                    'conditions': [], 'changes': [], 'audience': ['narrator', 'player'], 'asset_operations': []}],
                'blocking_questions': [], 'memory_request': None, 'memory_updates': [],
                'agenda_updates': [], 'continuation': None, 'roll_checks': []}

    def chronicle(self, arcs=False):
        chronology = self.root / 'chronology.json'
        chronology.write_text(json.dumps({'nodes': [{'id': 'raid', 'title': 'Local crisis'}]}), encoding='utf-8')
        with self.c.transaction() as con:
            w = WorldPlanner(con)
            w.initialize()
            w.install_chronicle([{'id': 'bandits', 'faction': 'Bandits', 'goal': 'Watch the road.',
                'segments': 4, 'progression': 1, 'status': 'in_progress', 'signs': 'Travelers worried.',
                'event': 'The road is blocked.', 'origin': 'canon',
                'reason': 'Local preparation.', 'references': ['chronology:raid']}],
                chronology.relative_to(ROOT).as_posix())
            if arcs:
                ArcRegistry(con).install({'version': 1, 'arcs': [{'id': 'route', 'title': 'Route',
                    'nodes': ['raid'], 'dependencies': [], 'opening_days': 0, 'end_days': 10,
                    'steps': ['Prepare the roadblock.', 'The bandits settle in.'], 'clock': None}],
                    'outside_arcs': {}}, {'raid'}, 1080)

    def world_response(self, work):
        exchanges = work['packet']['exchanges_to_summarize']
        return {'planner_schema_version': '2.0', 'player_summary': {'text': 'The guard waits at the door.',
                    'references': [e['reference'] for e in exchanges]} if exchanges else None, 'old_synthesis': None,
                'chronicle': {'clocks': [], 'manifestations': []}}

    def test_open_and_preview_do_not_write_campaign(self):
        before = self.c.path.read_bytes()
        self.assertEqual(preview(self.c, 7)['end_minute'], 1080 + 7 * 1440)
        Routine.create(self.root / 'other', self.c)
        self.assertEqual(self.c.path.read_bytes(), before)
        self.assertIsNone(self.c.resume()['session']['pending_action'])

    def test_command_line_tool_previews_opens_and_reports_status(self):
        import contextlib
        import io
        from novel_engine.scripts import routine as tool
        def call(*argv):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = tool.main(list(argv))
            return code, json.loads(out.getvalue())
        code, plan = call('--campaign', str(self.c.path), 'preview', '--days', '2')
        self.assertEqual(code, 0)
        folder = self.root / 'cli-routine'
        code, opened = call('--campaign', str(self.c.path), 'open', '--days', '2', '--folder', str(folder), '--legacy-mode')
        self.assertEqual((code, opened['status']), (0, 'ready'))
        code, status = call('status', '--folder', str(folder))
        self.assertEqual((code, status['status']), (0, 'ready'))
        code, failure = call('--campaign', str(self.c.path), 'open', '--folder', str(folder))
        self.assertEqual((code, failure['status']), (1, 'error'))

    def test_invalid_duration_and_existing_folder_refused(self):
        for value in (0, -1, 366, True, 1.5):
            with self.assertRaises(ValueError): preview(self.c, value)
        with self.assertRaises(FileExistsError): Routine.create(self.root / 'routine', self.c)

    def test_prepare_reuses_action_and_saved_packet_after_restart(self):
        first = self.r.prepare()
        pending = self.c.resume()['session']['pending_action']
        second = Routine(self.r.folder).prepare()
        self.assertEqual(first, second)
        self.assertEqual(pending, self.c.resume()['session']['pending_action'])
        self.assertTrue(Path(first['model']).is_file())
        self.assertIn('summoned', Path(first['instructions']).read_text(encoding='utf-8'))

    def test_two_days_finish_without_duplicate_turn(self):
        response = self.response()
        result = self.r.apply(response)
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(self.r.apply(response), result)
        self.r.apply(self.response())
        self.assertEqual(self.r.status()['status'], 'finished')
        self.assertEqual(self.c.state()['world_minute'], 1080 + 2880)
        self.assertEqual(self.r.report()['narration_count'], 2)
        self.assertNotIn('prepared', self.r.report())

    def test_crash_after_game_commit_recovers_same_receipt(self):
        self.work()
        response = self.response()
        manifest = self.r.manifest.read_bytes()
        accepted = self.r.apply(response)
        state = self.c.state()
        self.r.manifest.write_bytes(manifest)  # Simulation of a crash before the file receipt.
        again = Routine(self.r.folder).apply(response)
        self.assertEqual(again, accepted)
        self.assertEqual(self.c.state(), state)
        self.assertEqual(self.r.report()['narration_count'], 1)

    def test_other_pending_action_never_adopted(self):
        self.c.begin_action('I talk to the guard.')
        with self.assertRaisesRegex(ValueError, 'Another action'): self.r.prepare()
        self.assertEqual(self.c.resume()['session']['pending_action']['text'], 'I talk to the guard.')

    def test_external_progress_stops_before_new_action(self):
        self.c.apply(proposal(self.c.state(), 'A minute passes.', [event(self.c.state(),
            'Attente.', [], minute=1081)]))
        with self.assertRaisesRegex(ValueError, 'evolved'): self.r.prepare()
        self.assertTrue(self.r.status()['external_drift'])

    def test_stale_packet_refused(self):
        work = self.work()
        self.c.apply(proposal(self.c.state(), 'A minute passes.', [event(self.c.state(),
            'The guard adjusts his intention.', [{'operation': 'set', 'path': '/intentions/guard',
            'value': 'Watch the road.'}], minute=1080)]), background=True)
        with self.assertRaisesRegex(ValueError, 'Obsolete'): self.r.apply(self.response(work))

    def test_bound_prevents_overshoot_and_explicit_correction(self):
        w = self.work()
        before = self.c.state()
        with self.assertRaisesRegex(ValueError, 'bound'): self.r.apply(self.response(w, w['bound'] + 1))
        self.assertEqual(self.c.state(), before)
        with self.assertRaisesRegex(ValueError, 'already received'): self.r.apply(self.response(w))
        self.r.correct()
        self.assertEqual(self.r.apply(self.response())['status'], 'recorded')

    def test_limit_includes_arcs_truncation_interruptions_and_daily_report(self):
        prepared = {'state': {'world_minute': 1080}, 'world_agenda': {'events': [],
            'arcs_review_horizon': None, 'max_horizon_if_limited': None}}
        self.assertEqual(turn_limit(prepared, 9999, max_points=1), 2520)
        self.assertEqual(turn_limit(prepared, 9999, max_points=3), 5400)
        self.assertEqual(turn_limit(prepared, 9999), 9999)
        self.assertEqual(turn_limit(prepared, 9999, period_end=4000), 4000)
        self.assertEqual(turn_limit(prepared, 9999, fine_until=3000), 2520)
        self.assertEqual(turn_limit(prepared, 9999, fine_until=1080), 9999)
        prepared['world_agenda']['arcs_review_horizon'] = 1500
        self.assertEqual(turn_limit(prepared, 9999, max_points=1), 1500)
        prepared['world_agenda']['max_horizon_if_limited'] = 1400
        self.assertEqual(turn_limit(prepared, 9999, max_points=1), 1400)
        prepared['world_agenda']['events'] = [{'interrupt': True, 'due_minute': 1300}]
        self.assertEqual(turn_limit(prepared, 9999, max_points=1), 1300)
        self.assertEqual(turn_limit(prepared, 1200, max_points=1), 1200)

    def test_world_phase_is_manual_persistent_and_private(self):
        self.chronicle()
        work = self.work()
        self.assertEqual(work['phase'], 'world')
        response = self.world_response(work)
        minute = self.c.state()['world_minute']
        manifest = self.r.manifest.read_bytes()
        result = self.r.apply(response)
        self.assertEqual(result['status'], 'recorded_world')
        self.assertEqual(result['remote_calls'], 0)
        self.assertEqual(self.c.state()['world_minute'], minute)
        self.r.manifest.write_bytes(manifest)
        Routine(self.r.folder).apply(response)
        self.assertEqual(self.work()['phase'], 'turn')
        self.assertEqual(self.r.report()['narration'], '')
        with self.c.transaction() as con:
            row = con.execute('SELECT status,metrics FROM planner_reviews').fetchone()
            self.assertEqual(row['status'], 'applied')
            self.assertEqual(json.loads(row['metrics'])['model_calls'], 0)

    def test_due_arc_cannot_be_skipped(self):
        self.chronicle(arcs=True)
        work = self.work()
        response = self.world_response(work)
        response['chronicle']['arcs'] = []
        before = self.c.state()
        with self.assertRaisesRegex(ValueError, 'without a decision'): self.r.apply(response)
        self.assertEqual(self.c.state(), before)
        self.r.correct()
        work = self.work()
        arc = work['packet']['chronicle']['arcs_registry']['arcs'][0]
        response['chronicle']['arcs'] = [{'id': 'route', 'expected_revision': 0, 'status': 'in_progress',
            'step': 1, 'motif': 'progression', 'reason': 'The bandits gather their means on site.',
            'world_fact': 'The bandits scout the road and prepare a roadblock.',
            'next_review': 3960, 'references': [arc['reference'], 'chronology:raid']}]
        self.r.apply(response)
        self.assertEqual(self.work()['bound'], 2520)
        self.assertNotIn('bandits scout', self.r.report()['narration'])

    def test_question_stops_delegation(self):
        response = self.response()
        response.update(events=[], narration='', blocking_questions=['Accept this alliance?'])
        result = self.r.apply(response)
        self.assertEqual(result['status'], 'question')
        self.assertEqual(self.r.status()['status'], 'decision_required')
        self.assertEqual(self.r.prepare()['status'], 'decision_required')

    def test_zero_progress_stops_instead_of_looping(self):
        w = self.work()
        self.r.apply(self.response(w, 1080))
        self.assertEqual(self.r.status()['status'], 'to_examine')

    def test_remote_generator_is_unavailable(self):
        with self.assertRaisesRegex(RuntimeError, 'no remote generator'): NoRemote().generate('test')

    def test_interrupt_stops_even_when_routine_time_remains(self):
        with self.c.transaction() as con:
            agenda = Agenda(con)
            agenda.initialize()
            agenda._put({'entry_id': 'encounter:embassy', 'expected_revision': -1,
                'title': 'Arrival of an embassy', 'actor': 'Ambassadeur', 'location': 'gate',
                'kind': 'risk', 'due_minute': 1110, 'conditions': [],
                'references': ['demo:initial_configuration'], 'audience': ['narrator', 'player'],
                'interrupt': True, 'status': 'planned', 'reason': 'Demande policy important.',
                'outcome_event_index': None}, 'fixture', 1080)
        work = self.work()
        self.assertEqual(work['bound'], 1110)
        response = self.response(work)
        response['narration'] = 'An embassy arrives and asks for an alliance. You may answer it.'
        response['events'][0]['description'] = response['narration']
        response['events'][0]['causes'].append('agenda:encounter:embassy')
        response['agenda_updates'] = [{'entry_id': 'encounter:embassy', 'status': 'finished',
            'reason': 'Embassy arrived, reception accomplished; political answer left to the player.',
            'references': ['agenda:encounter:embassy'], 'outcome_event_index': 0,
            'creation': None, 'due_minute': None}]
        response['continuation'] = {'goal': 'Continue the routine.', 'remaining': 'An alliance is proposed.',
                                    'status': 'suspended', 'event_index': 0}
        self.assertEqual(self.r.apply(response)['status'], 'recorded')
        self.assertEqual(self.r.status()['status'], 'decision_required')
        self.assertEqual(self.c.state()['world_minute'], 1110)

    def test_invalid_world_does_not_consume_or_change_a_review(self):
        self.chronicle()
        work = self.work()
        response = self.world_response(work)
        response['player_summary']['references'] = []
        with self.assertRaises(ValueError): self.r.apply(response)
        with self.c.transaction() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM planner_reviews').fetchone()[0], 0)
        self.assertTrue((Path(self.r.prepare()['step']) / 'answer.json').exists())

    def test_full_week_has_exact_fixed_target(self):
        r = Routine.create(self.root / 'week', self.c, days=7, policy=LEGACY_POLICY)
        for day in range(7):
            work = read_json(Path(r.prepare()['step']) / 'work.json')
            r.apply(self.response(work))
        self.assertEqual(r.status()['status'], 'finished')
        self.assertEqual(self.c.state()['world_minute'], 1080 + 7 * 1440)
        self.assertEqual(r.report()['narration_count'], 7)

    def test_response_cannot_introduce_unknown_source(self):
        response = self.response()
        response['events'][0]['causes'] = ['source:invented']
        before = self.c.state()
        with self.assertRaisesRegex(ValueError, 'authorized reference'): self.r.apply(response)
        self.assertEqual(self.c.state(), before)

    def test_recover_after_begin_action_before_packet_write(self):
        data = read_json(self.r.manifest)
        data['active'] = {'request_id': 'action_stable_after_crash', 'file': None}
        write_json(self.r.manifest, data)
        self.c.begin_action('Routine already saved before the interruption.', 'action_stable_after_crash')
        self.assertEqual(self.r.prepare()['phase'], 'turn')
        self.assertEqual(self.c.resume()['session']['pending_action']['request_id'], 'action_stable_after_crash')

    def test_missing_campaign_never_creates_empty_database(self):
        self.c.path.unlink()
        with self.assertRaisesRegex(ValueError, 'missing'): Routine(self.r.folder)
        with self.assertRaisesRegex(ValueError, 'missing'): self.r.apply({'narration': 'Test'})
        self.assertFalse(self.c.path.exists())

    def test_action_activates_long_ellipsis_review(self):
        self.assertTrue(long_wait(self.r._action()))

    # Long routine: adaptive step, daily bulletin, periods and signals.
    def long(self, days=10, **policy):
        return Routine.create(self.root / ('long%d' % len(list(self.root.iterdir()))), self.c, days=days,
                              policy=policy)

    def long_response(self, routine, minute=None, signals=None, bulletins=True):
        work = read_json(Path(routine.prepare()['step']) / 'work.json')
        end = work['bound'] if minute is None else minute
        response = self.response(work, end)
        lines = ['%s: nothing important reaches Mira.' % p['marker']
                 for p in daily_points(work['prepared']['state']['world_minute'], end)]
        response['narration'] = '\n'.join(lines) if bulletins else 'Rien.'
        response['events'][0]['description'] = 'The days pass with no news.'
        response['routine_signals'] = signals or []
        return work, response

    def test_default_policy_steps_to_period_end_with_daily_bulletin(self):
        r = self.long()
        work, response = self.long_response(r)
        self.assertEqual(work['bound'], 1080 + 7 * 1440)
        self.assertEqual([p['marker'] for p in work['packet']['routine']['daily_points']],
                         ['D%d' % d for d in range(1, 8)])
        result = r.apply(response)
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(result['review_period']['period'], 1)
        self.assertEqual(result['review_period']['steps'], 1)
        self.assertIn('D7:', result['review_period']['narration'])
        self.assertEqual(r.status()['status'], 'in_progress')
        work, response = self.long_response(r)
        self.assertEqual(work['bound'], 1080 + 10 * 1440)
        self.assertEqual(r.apply(response)['review_period']['period'], 2)
        self.assertEqual(r.status()['status'], 'finished')
        self.assertEqual(r.status()['closed_periods'], 2)

    def test_missing_bulletin_is_refused_without_consuming_the_step(self):
        r = self.long()
        work, response = self.long_response(r, bulletins=False)
        before = self.c.state()
        with self.assertRaisesRegex(ValueError, 'Daily bulletin missing.*D1'): r.apply(response)
        self.assertEqual(self.c.state(), before)
        self.assertFalse((Path(r.prepare()['step']) / 'answer.json').exists())
        _, response = self.long_response(r)
        self.assertEqual(r.apply(response)['status'], 'recorded')

    def test_signals_are_required_and_validated(self):
        r = self.long()
        work, response = self.long_response(r)
        del response['routine_signals']
        with self.assertRaisesRegex(ValueError, 'routine_signals is required'): r.apply(response)
        response['routine_signals'] = [{'level': 'severe', 'category': 'other', 'world_minute': 2000,
                                        'reason': 'A long enough reason.'}]
        with self.assertRaisesRegex(ValueError, 'unknown'): r.apply(response)
        response['routine_signals'] = [{'level': 'info', 'category': 'other', 'world_minute': 99999,
                                        'reason': 'A long enough reason.'}]
        with self.assertRaisesRegex(ValueError, 'outside the period'): r.apply(response)

    def test_critical_signal_stops_routine_at_its_minute(self):
        r = self.long()
        signal = {'level': 'critical', 'category': 'base_threat', 'world_minute': 4000,
                  'reason': 'An army is reported in front of the Bastion.'}
        _, late = self.long_response(r, minute=5000, signals=[signal])
        with self.assertRaisesRegex(ValueError, 'no event after'): r.apply(late)
        _, response = self.long_response(r, minute=4000, signals=[signal])
        result = r.apply(response)
        self.assertEqual(result['routine_stop'][0]['category'], 'base_threat')
        self.assertEqual(r.status()['status'], 'decision_required')
        self.assertEqual(self.c.state()['world_minute'], 4000)

    def test_alert_forces_daily_steps_then_returns_to_adaptive(self):
        r = self.long(alert_days=1)
        signal = {'level': 'alert', 'category': 'arc_in_crisis', 'world_minute': 3000,
                  'reason': 'The bandits block the north road.'}
        _, response = self.long_response(r, minute=3000, signals=[signal])
        r.apply(response)
        self.assertEqual(r.status()['status'], 'in_progress')
        work, response = self.long_response(r)
        self.assertEqual(work['bound'], 3960)  # next daily point: D2 at 6 pm
        r.apply(response)
        work, response = self.long_response(r)
        self.assertEqual(work['bound'], 5400)
        r.apply(response)
        work, _ = self.long_response(r)
        self.assertGreater(work['bound'], 5400 + 1440)  # alert over: adaptive step

    def test_alert_can_also_stop_when_requested(self):
        r = self.long(stop_on=['alert'])
        signal = {'level': 'alert', 'category': 'message_for_player', 'world_minute': 3000,
                  'reason': 'A messenger asks to see Mira.'}
        _, response = self.long_response(r, minute=3000, signals=[signal])
        r.apply(response)
        self.assertEqual(r.status()['status'], 'decision_required')

    def test_period_pause_resumes_explicitly(self):
        r = self.long(pause_period=True)
        _, response = self.long_response(r)
        r.apply(response)
        self.assertEqual(r.status()['status'], 'period_finished')
        self.assertEqual(r.prepare(resume=False)['status'], 'period_finished')
        self.assertEqual(r.prepare()['phase'], 'turn')
        self.assertEqual(r.status()['status'], 'in_progress')

    def test_year_long_routine_is_accepted_and_policy_validated(self):
        r = self.long(days=365)
        self.assertEqual(r.status()['minutes_left'], 365 * 1440)
        self.assertEqual(r.prepare()['end_minute_step'], 1080 + 7 * 1440)
        for bad in ({'arcs_review_days': 1}, {'stop_on': ['severe']}, {'points_per_step': 0},
                    {'unknown': 1}):
            with self.assertRaises(ValueError): normalize_policy(bad)
        self.assertEqual(normalize_policy({'stop_on': ['alert']})['stop_on'], ['alert', 'critical'])

    def test_extended_arc_review_delay_is_opt_in(self):
        self.chronicle(arcs=True)
        r = self.long(arcs_review_days=5)
        work = read_json(Path(r.prepare()['step']) / 'work.json')
        self.assertEqual(work['packet']['chronicle']['arcs_registry']['max_review_delay'], 5 * 1440)
        response = self.world_response(work)
        arc = work['packet']['chronicle']['arcs_registry']['arcs'][0]
        response['chronicle']['arcs'] = [{'id': 'route', 'expected_revision': 0, 'status': 'in_progress',
            'step': 1, 'motif': 'progression', 'reason': 'The bandits gather their means on site.',
            'world_fact': 'The bandits scout the road and prepare a roadblock.',
            'next_review': 1080 + 5 * 1440, 'references': [arc['reference'], 'chronology:raid']}]
        self.assertEqual(r.apply(response)['status'], 'recorded_world')
        self.assertEqual(r.prepare()['end_minute_step'], 1080 + 5 * 1440)

    def test_arc_review_delay_defaults_to_two_days(self):
        self.chronicle(arcs=True)
        other = self.long(days=5)
        w2 = read_json(Path(other.prepare()['step']) / 'work.json')
        self.assertEqual(w2['packet']['chronicle']['arcs_registry']['max_review_delay'], 2 * 1440)


if __name__ == '__main__':
    unittest.main()
