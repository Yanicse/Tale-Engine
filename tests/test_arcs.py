"""Arcs without a date, off-screen progress, resumes and bridge with no game mutation."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

import test_planner as fixtures
from novel_engine.adapters import ModelUnavailable
from novel_engine.arcs import ArcRegistry, LONG_REVIEW_DELAY, REVIEW_DELAY
from novel_engine.demo import event, proposal
from novel_engine.agenda import Agenda
from novel_engine.planner import WorldPlanner, planner_schema
from novel_engine.scripts.install_arcs import install, preview
from novel_engine.scripts.verify_game_kit import check_schema


PLAN = {'version': 1, 'arcs': [{
    'id': 'route', 'title': 'Road crisis', 'nodes': ['raid'], 'dependencies': [],
    'opening_days': 0, 'end_days': 5, 'steps': ['The bandits gather.', 'The bandits attack.'],
    'clock': None}], 'outside_arcs': {}}


class ArcTests(unittest.TestCase):
    setUp = fixtures.PlannerTests.setUp
    tearDown = fixtures.PlannerTests.tearDown
    chronicle = fixtures.PlannerTests.chronicle

    def register(self, plan=None):
        self.chronicle()
        self.plan = deepcopy(plan or PLAN)
        return install(self.c, self.plan)

    def response(self, packet, response, **fields):
        arc = packet['chronicle']['arcs_registry']['arcs'][0]
        item = {'id': arc['id'], 'expected_revision': arc['revision'], 'status': 'in_progress',
                'step': arc['step'] + 1, 'motif': 'progression',
                'reason': 'The bandits have their means and gather.',
                'world_fact': "The bandits gather in the hills, away from the traveler's presence.",
                'next_review': packet['current_state']['world_minute'] + REVIEW_DELAY,
                'references': [arc['reference'], 'chronology:raid']}
        item.update(fields)
        response['chronicle']['arcs'] = [item]
        return response

    def test_install_is_explicit_idempotent_and_preserves_game(self):
        self.chronicle()
        before = self.c.path.read_bytes()
        report = preview(self.c.path, PLAN)
        self.assertFalse(report['mutation'])
        self.assertEqual(self.c.path.read_bytes(), before)
        result = install(self.c, PLAN)
        self.assertEqual(result['fingerprint_before'], result['fingerprint_after'])
        self.assertTrue(Path(result['backup']).is_file())
        self.assertEqual(install(self.c, PLAN)['arcs_added'], 0)
        with self.c.transaction() as con:
            state = self.c._state(con)
            state['world_minute'] += 20
            con.execute('UPDATE campaign SET state=?', (json.dumps(state),))
        # Rereading a registry installed later never shifts its windows.
        self.assertEqual(preview(self.c.path, PLAN)['arcs'], report['arcs'])
        with self.c.transaction() as con:
            self.assertEqual(len(ArcRegistry(con).facts()), 0)
            changed = deepcopy(PLAN); changed['arcs'][0]['end_days'] = 6
            with self.assertRaisesRegex(ValueError, 'different'):
                ArcRegistry(con).install(changed, {'raid'}, report['world_minute'])

    def test_an_arc_saved_with_the_old_status_name_reads_as_to_prepare(self):
        self.chronicle()
        install(self.c, PLAN)
        with self.c.transaction() as con:
            row = con.execute('SELECT id,payload FROM chronicle_arcs').fetchone()
            payload = json.loads(row['payload'])
            self.assertEqual(payload['status'], 'to_prepare')
            payload['status'] = 'a_prepare'
            con.execute('UPDATE chronicle_arcs SET payload=? WHERE id=?', (json.dumps(payload), row['id']))
            self.assertEqual({a['status'] for a in ArcRegistry(con).entries()}, {'to_prepare'})

    def test_incomplete_coverage_cycle_and_unknown_nodes_refused(self):
        with self.assertRaisesRegex(ValueError, 'without follow-up'):
            ArcRegistry.verify(PLAN, {'raid', 'other'})
        bad = deepcopy(PLAN); bad['arcs'][0]['dependencies'] = ['route']
        with self.assertRaisesRegex(ValueError, 'cycle'):
            ArcRegistry.verify(bad, {'raid'})
        with self.assertRaisesRegex(ValueError, 'missing'):
            ArcRegistry.verify(PLAN, {'other'})

    def test_unknown_date_progresses_and_fact_stays_private(self):
        self.register()
        self.planner.transform = self.response
        before = self.c.state()
        result = self.loop.run_turn('Hello.')
        self.assertEqual(result['status'], 'recorded')
        with self.c.transaction() as con:
            a = ArcRegistry(con).entries()[0]
            fact = ArcRegistry(con).facts()[0]
            self.assertEqual((a['step'], a['status']), (1, 'in_progress'))
            self.assertEqual(fact['world_minute'], before['world_minute'])
            self.assertEqual(fact['audience'], ['narrator'])
            self.assertFalse(fact['player_knowledge'])
            self.assertIn('private_world_facts', WorldPlanner(con).narrator_packet())
            schema = planner_schema(self.planner.packets[0])
            check_schema(schema)
            self.assertIn('arcs', schema['properties']['chronicle']['required'])
        self.assertNotIn('bandits gather', json.dumps(self.c.journal('player')))
        self.assertEqual(self.c.state()['positions'], before['positions'])
        self.assertEqual(self.c.state()['resources'], before['resources'])

    def test_missing_arc_decision_rolls_back_summary_clocks_and_facts(self):
        self.register()
        self.planner.transform = lambda p, r: {**r, 'chronicle': {**r['chronicle'], 'arcs': []}}
        before = self.c.state()
        with self.c.transaction() as con:
            summaries = con.execute('SELECT COUNT(*) FROM planner_summaries').fetchone()[0]
        with self.assertRaisesRegex(ModelUnavailable, 'without a decision'):
            self.loop.run_turn('Hello.')
        with self.c.transaction() as con:
            self.assertEqual(json.loads(con.execute('SELECT state FROM campaign').fetchone()[0]), before)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM planner_summaries').fetchone()[0], summaries)
            self.assertEqual(ArcRegistry(con).entries()[0]['revision'], 0)
            self.assertEqual(ArcRegistry(con).facts(), [])

    def test_unknown_date_is_not_a_valid_obstacle(self):
        self.register()
        self.planner.transform = lambda p, r: self.response(p, r, step=0, status='to_prepare',
            motif='obstacle', world_fact=None, reason='The precise date is unknown in the novel.')
        with self.assertRaisesRegex(ModelUnavailable, 'unknown date'):
            self.loop.run_turn('Hello.')

    def test_long_activity_extends_arc_review_for_that_turn_only(self):
        self.register()
        seen = []
        def transform(packet, response):
            seen.append(packet['chronicle']['arcs_registry'])
            far = packet['current_state']['world_minute'] + 5 * 1440
            return self.response(packet, response, next_review=far)
        self.planner.transform = transform
        result = self.loop.run_turn('I train until something interrupts me.')
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(seen[0]['max_review_delay'], LONG_REVIEW_DELAY)
        self.assertIn('delay_instruction', seen[0])
        with self.c.transaction() as con:
            entry = ArcRegistry(con).entries()[0]
        self.assertGreater(entry['next_review'] - self.c.state()['world_minute'], REVIEW_DELAY)

    def test_ordinary_action_keeps_two_day_arc_review_limit(self):
        self.register()
        seen = []
        def transform(packet, response):
            seen.append(packet['chronicle']['arcs_registry'])
            far = packet['current_state']['world_minute'] + 5 * 1440
            return self.response(packet, response, next_review=far)
        self.planner.transform = transform
        with self.assertRaisesRegex(ModelUnavailable, 'fictional days'):
            self.loop.run_turn('Hello.')
        self.assertEqual(seen[0]['max_review_delay'], REVIEW_DELAY)
        self.assertNotIn('delay_instruction', seen[0])

    def test_long_ellipsis_cannot_skip_arc_review(self):
        self.register()
        self.planner.transform = self.response
        self.loop.run_turn('Hello.')
        state = self.c.state()
        with self.c.transaction() as con:
            horizon = ArcRegistry(con).horizon(state['world_minute'])
        e = event(state, 'The journey lasts three days.', [], minute=horizon + 1)
        result = self.c.apply(proposal(state, 'I travel.', [e]))
        self.assertEqual(result['status'], 'rejected')
        self.assertIn('review of the world arcs', result['reason'])
        self.assertEqual(self.c.state(), state)
        e = event(state, 'Time passes until the world follow-up.', [], minute=horizon)
        self.assertEqual(self.c.apply(proposal(state, 'I travel.', [e]))['status'], 'recorded')
        with self.c.transaction() as con:
            self.assertIn('arcs_to_follow', WorldPlanner(con).reasons('Hello.'))
        self.assertIsNone(self.c.world_agenda('player')['arcs_review_horizon'])

    def test_completion_requires_all_stages_and_is_not_repeated(self):
        self.register()
        self.planner.transform = self.response
        self.loop.run_turn('Hello.')
        state = self.c.state()
        self.c.apply(proposal(state, 'Attendre.', [event(state, 'Time passes.', [], minute=state['world_minute'] + REVIEW_DELAY)]))
        self.planner.transform = lambda p, r: self.response(p, r, status='achieved', next_review=None,
            world_fact='The bandits close the road and ransom the convoys.')
        self.assertEqual(self.loop.run_turn('Hello.')['status'], 'recorded')
        calls = self.planner.calls
        self.loop.run_turn('One more detail.')
        self.assertEqual(self.planner.calls, calls)
        with self.c.transaction() as con:
            self.assertEqual(len(ArcRegistry(con).facts()), 2)
            self.assertEqual(ArcRegistry(con).entries()[0]['status'], 'achieved')

    def test_dependencies_cannot_be_completed_in_same_review(self):
        plan = deepcopy(PLAN)
        child = {**deepcopy(plan['arcs'][0]), 'id': 'suite', 'dependencies': ['route']}
        plan['arcs'].append(child)
        self.register(plan)
        def invalid(p, r):
            self.response(p, r)
            a = p['chronicle']['arcs_registry']['arcs'][1]
            r['chronicle']['arcs'].append({**r['chronicle']['arcs'][0], 'id': a['id'], 'references': [a['reference']]})
            return r
        self.planner.transform = invalid
        with self.assertRaisesRegex(ModelUnavailable, 'dependencies'):
            self.loop.run_turn('Hello.')

    def test_divergence_needs_actual_campaign_evidence(self):
        self.register()
        self.planner.transform = lambda p, r: self.response(p, r, status='diverted', step=0,
            motif='divergence', next_review=None)
        with self.assertRaisesRegex(ModelUnavailable, 'campaign cause'):
            self.loop.run_turn('Hello.')

    def add_evidence(self):
        state = self.c.state()
        e = event(state, 'The guard blocks the pass after a rockslide that was observed.', [])
        result = self.c.apply(proposal(state, 'Observe the pass.', [e]))
        self.assertEqual(result['status'], 'recorded')
        return e['event_id']

    def test_real_obstacle_is_bounded_then_progression_resumes(self):
        self.register()
        proof = self.add_evidence()
        def obstacle(p, r):
            return self.response(p, r, status='to_prepare', step=0, motif='obstacle',
                world_fact=None, reason='The pass is physically blocked by the observed rockslide.',
                references=[proof])
        self.planner.transform = obstacle
        for i in range(2):
            self.assertEqual(self.loop.run_turn('Hello.')['status'], 'recorded')
            state = self.c.state()
            self.c.apply(proposal(state, 'Attendre.', [event(state, 'Time passes.', [], minute=state['world_minute'] + REVIEW_DELAY)]))
        with self.assertRaisesRegex(ModelUnavailable, 'postponements exhausted'):
            self.loop.run_turn('Hello.')
        self.planner.transform = self.response
        self.assertEqual(self.loop.run_turn(resume=True, retry=True)['status'], 'recorded')
        with self.c.transaction() as con:
            self.assertEqual(ArcRegistry(con).entries()[0]['obstacle_postponements'], 0)

    def test_intervention_can_divert_arc_with_persisted_consequence(self):
        self.register()
        proof = self.add_evidence()
        self.planner.transform = lambda p, r: self.response(p, r, status='diverted', step=0,
            motif='divergence', next_review=None, references=[proof],
            world_fact='The bandits abandon the blocked pass and move their activity to another valley.')
        self.assertEqual(self.loop.run_turn('Hello.')['status'], 'recorded')
        with self.c.transaction() as con:
            self.assertEqual(ArcRegistry(con).entries()[0]['status'], 'diverted')
            self.assertEqual(ArcRegistry(con).horizon(self.c._state(con)['world_minute']), None)
            self.assertEqual(ArcRegistry(con).facts()[0]['references'], [proof])

    def test_crash_after_response_reuses_fact_exactly_once(self):
        self.register()
        self.planner.transform = self.response
        with patch.object(WorldPlanner, 'apply_review', side_effect=OSError('Stop after receipt')):
            with self.assertRaises(OSError):
                self.loop.run_turn('Hello.')
        calls = self.planner.calls
        self.assertEqual(self.loop.run_turn(resume=True)['status'], 'recorded')
        self.assertEqual(self.planner.calls, calls)
        with self.c.transaction() as con:
            self.assertEqual(len(ArcRegistry(con).facts()), 1)
            self.assertEqual(ArcRegistry(con).entries()[0]['revision'], 1)

    def test_linked_final_stage_waits_for_actual_manifestation(self):
        plan = deepcopy(PLAN); plan['arcs'][0]['clock'] = 'bandits'
        self.register(plan)
        self.planner.transform = self.response
        self.loop.run_turn('Hello.')
        state = self.c.state()
        self.c.apply(proposal(state, 'Attendre.', [event(state, 'Time passes.', [], minute=state['world_minute'] + REVIEW_DELAY)]))
        self.planner.transform = lambda p, r: self.response(p, r, status='achieved', next_review=None)
        with self.assertRaisesRegex(ModelUnavailable, 'effective resolution'):
            self.loop.run_turn('Hello.')
        # Fixture: big event already resolved, current references replaced by the closing.
        with self.c.transaction() as con:
            planner = WorldPlanner(con)
            clock = {**planner.clocks()[0], 'progression': 4, 'status': 'burst'}
            planner._put_clock(clock, 'fixture')
            entry = {'entry_id': 'chronicle:attack', 'title': 'Attaque', 'actor': 'Bandits',
                     'location': 'Route', 'kind': 'risk', 'due_minute': state['world_minute'],
                     'conditions': [], 'references': ['chronicle:bandits:1'], 'audience': ['narrator'],
                     'interrupt': True, 'status': 'planned', 'reason': 'Big event scheduled.',
                     'outcome_event_index': None}
            Agenda(con)._put(entry, 'fixture', state['world_minute'])
            Agenda(con)._put({**entry, 'status': 'finished', 'references': ['agenda:chronicle:attack']}, 'fixture2', state['world_minute'], revision=1)
        self.assertEqual(self.loop.run_turn(resume=True, retry=True)['status'], 'recorded')

    def test_register_installation_refuses_pending_action(self):
        self.chronicle()
        self.c.prepare_turn('Action to keep.')
        with self.assertRaisesRegex(ValueError, 'pending action'):
            install(self.c, PLAN)

    def test_clock_cannot_bypass_arc_dependencies(self):
        plan = deepcopy(PLAN)
        plan['arcs'].append({**deepcopy(plan['arcs'][0]), 'id': 'suite',
                             'clock': 'bandits', 'dependencies': ['route']})
        self.register(plan)
        with self.c.transaction() as con:
            p = WorldPlanner(con)
            p._put_clock({**p.clocks()[0], 'progression': 3}, 'fixture')
        def premature(p, r):
            self.response(p, r)
            a = p['chronicle']['arcs_registry']['arcs'][1]
            r['chronicle']['arcs'].append({**r['chronicle']['arcs'][0], 'id': a['id'],
                'status': 'to_prepare', 'step': 0, 'motif': 'dependency', 'world_fact': None,
                'references': [a['reference']]})
            r['chronicle']['clocks'] = [fixtures.advance(p, status='burst')]
            r['chronicle']['manifestations'] = [fixtures.sign(p, kind='event')]
            return r
        self.planner.transform = premature
        with self.assertRaisesRegex(ModelUnavailable, "before the arc's dependencies"):
            self.loop.run_turn('Hello.')
        with self.c.transaction() as con:
            self.assertEqual(ArcRegistry(con).facts(), [])
            self.assertEqual(WorldPlanner(con).clocks()[0]['status'], 'in_progress')

    def test_named_old_arc_fact_is_recalled_before_unrelated_recent_facts(self):
        plan = deepcopy(PLAN)
        plan['arcs'].append({**deepcopy(plan['arcs'][0]), 'id': 'other', 'title': 'Harbor crisis'})
        self.register(plan)
        with self.c.transaction() as con:
            for i in range(14):
                ref = 'arc_fact:fixture:' + str(i)
                fact = {'reference': ref, 'arc_id': 'route' if i == 0 else 'other',
                        'description': 'A fact about the road.' if i == 0 else 'A harbor matter.',
                        'world_minute': i, 'audience': ['narrator'], 'player_knowledge': False, 'references': []}
                con.execute('INSERT INTO chronicle_arc_facts VALUES (?,?,?,?)', (ref, fact['arc_id'], i, json.dumps(fact)))
            self.assertTrue(all(f['arc_id'] == 'other' for f in ArcRegistry(con).facts()))
            recalled = WorldPlanner(con).narrator_packet('What is going on on the road?')['private_world_facts']
            self.assertEqual(recalled[0]['arc_id'], 'route')
            self.assertEqual(len(recalled), 12)

    def test_chronology_preserves_time_evidence_and_partial_order(self):
        data = {'nodes': [{'id': 'raid', 'title': 'Raid', 'phase': 'branch',
                           'relative_time': 'After the meeting, day unknown.', 'evidence': ['proof:1']}],
                'edges': [{'before': 'meeting', 'after': 'raid'}]}
        (Path(self.temp.name) / 'chronology.json').write_text(json.dumps(data), encoding='utf-8')
        self.chronicle()
        with self.c.transaction() as con:
            node = WorldPlanner(con).chronology_nodes()[0]
        self.assertEqual(node['after'], ['meeting'])
        self.assertEqual(node['proofs'], ['proof:1'])
        self.assertIn('day unknown', node['time_marker'])



if __name__ == '__main__':
    unittest.main()
