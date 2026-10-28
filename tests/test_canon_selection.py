"""Bounded canonical search and separation of played facts, on a fictional canon."""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from novel_engine.adapters import ModelUnavailable
from novel_engine.agenda import Agenda
from novel_engine.campaign import Campaign
from novel_engine.canon_selection import CanonSelector, BUDGETS, LIMITS, select_for_turn
from novel_engine.contracts import check_json
from novel_engine.demo import initial_state, configuration, event, proposal
from novel_engine.memory import ContextBuilder
from novel_engine.narration_codex import response_schema
from novel_engine.narrative import NarrativeLoop
from novel_engine.planner import WorldPlanner
from novel_engine.scripts.fictional_canon_fixture import create_fixture

GUILD_RANKS = 'v02_p0022_0036:c15'
EXACT = 'v05_p0240_0246:c2'


class FakeNarrator:
    def __init__(self):
        self.calls = 0
        self.last_metrics = {}
        self.transform = None

    def generate(self, prompt):
        self.calls += 1
        packet = json.loads(prompt.rsplit('\nCURRENT TURN\n', 1)[1])
        response = {'narration': 'The guard answers your question.', 'events': [], 'blocking_questions': []}
        return self.transform(packet, response) if self.transform else response


def build_canon(path):
    """Invented canon: names, ranks and episodes exist nowhere outside this test."""
    create_fixture(path)
    entities = [('maera', 'character', 'Maera'), ('tolven', 'character', 'Tolven'),
                ('brenn', 'character', 'Brenn'), ('ysolde', 'character', 'Ysolde'),
                ('orrin', 'character', 'Orrin Vale'), ('vale_company', 'faction', 'Vale Company'),
                ('harrow', 'place', 'Harrow Village')]
    aliases = {'harrow': ['Harrow'], 'vale_company': ['Orrin Vale']}
    claims = [
        (GUILD_RANKS, 'guild', 'The local Guild sorts its adventurers into three ranks: copper, silver and gold.',
         'rank_hierarchy', None, 2),
        ('v02_p0022_0036:c16', 'guild', 'Adventurers register at the local Guild desk in the morning.',
         'sheet_function', None, 2),
        ('v05_p0100_0104:c1', 'tolven', 'Tolven is a butler-warden whose abilities include a strong shield technique.',
         'sheet_class_level', None, 5),
        ('v05_p0100_0104:c2', 'tolven', 'Tolven is loyal and kind-hearted; his karma is positive.',
         'sheet_karma', None, 5),
        ('v05_p0100_0104:c3', 'tolven', 'Tolven learned a healing spell from a travelling priest.',
         'ability', None, 5),
        ('v03_p0050_0052:c1', 'brenn', 'Brenn commands the lower floors and values order and loyalty.',
         'sheet_function', None, 3),
        ('v03_p0050_0052:c2', 'ysolde', 'Ysolde serves Brenn as a disciplined guard with a calm character.',
         'sheet_function', None, 3),
        (EXACT, 'maera', 'Maera once served in the harbor watch.', 'history', None, 5),
        ('v04_p0010_0012:c1', 'harrow', 'A merchant caravan stops at Harrow Village and its elder greets the travelers.',
         'visit_event', 'Day 3', 4),
        ('v04_p0020_0022:c1', 'orrin', 'Orrin Vale keeps a tally of every contract.', 'habit', None, 4),
        ('v04_p0020_0022:c2', 'vale_company', 'The Vale Company hires escorts for long journeys.', 'sheet_function', None, 4),
        ('v06_p0001_0003:c1', 'town', 'Bright Town grain merchants hire carts to transport sacks to the south road.',
         'trade', None, 6)]
    with closing(sqlite3.connect(path)) as con, con:
        # Columns and table read by the public memory search, absent from the minimal fixture.
        for column in ('kind TEXT', 'quote TEXT', 'image TEXT'):
            con.execute('ALTER TABLE sources ADD COLUMN ' + column)
        con.execute('CREATE TABLE known_by(claim_id TEXT, entity_id TEXT)')
        for ident, kind, name in entities:
            con.execute('INSERT INTO entities VALUES (?,?,?,?)', (ident, kind, name, 'named'))
            con.execute('INSERT INTO aliases VALUES (?,?)', (ident, name))
            for alias in aliases.get(ident, ()):
                con.execute('INSERT INTO aliases VALUES (?,?)', (ident, alias))
            con.execute('INSERT INTO canonical_entities VALUES (?,?)', (ident, ident))
        names = {ident: name for ident, _, name in entities}
        names.update(guild='Local Guild', town='Bright Town')
        for ref, subject, text, predicate, world_time, volume in claims:
            con.execute('INSERT INTO consolidated_usable_claims VALUES (?,?,?,?,?,?,?)',
                        (ref, subject, text, predicate, world_time, 'canon', ref))
            con.execute('INSERT INTO sources(claim_id,volume,pdf_page) VALUES (?,?,1)', (ref, volume))
            con.execute('INSERT INTO consolidated_fts VALUES (?,?,?,?,?)',
                        (ref, names[subject], names[subject], text, predicate))
    return Path(path)


class CanonSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = ROOT / '.tmp-tests'
        base.mkdir(exist_ok=True)
        cls.shared = tempfile.TemporaryDirectory(dir=base)
        cls.snapshot = build_canon(Path(cls.shared.name) / 'canon.sqlite')

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def setUp(self):
        base = ROOT / '.tmp-tests'
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        state = initial_state()
        state['versions'].update(canon_revision=125, canon_snapshot=str(self.snapshot))
        config = configuration()
        config['canon_selection'] = {'enabled': True, 'version': 1}
        self.c = Campaign(Path(self.temp.name) / 'novel_engine.sqlite').create(state, config)
        self.c.open_scene('You stand in front of the local Guild of Bright Town.')
        self.selector = CanonSelector(state, config)

    def tearDown(self):
        self.temp.cleanup()

    def test_disabled_campaign_keeps_legacy_path(self):
        config = configuration()
        self.assertIsNone(select_for_turn(self.c.state(), config, 'Maera', ''))

    def test_automatic_selection_without_query_and_prompt_receives_it(self):
        prepared = self.c.prepare_turn('What are the ranks of the Guild?', narrative_memory=True)
        packet = prepared['documentary_canon']
        self.assertTrue(packet['facts'])
        self.assertIn('canon:' + GUILD_RANKS, [f['reference'] for f in packet['facts']])
        prompt = NarrativeLoop(self.c, FakeNarrator()).build_prompt(prepared)
        received = json.loads(prompt.rsplit('\nCURRENT TURN\n', 1)[1])
        self.assertEqual(received['documentary_canon'], packet)
        self.assertTrue(all(f['reference'] in received['allowed_references'] for f in packet['facts']))

    def test_budgets_include_envelope_and_no_remote_calls(self):
        for role in BUDGETS:
            packet = self.selector.select('Tolven and Brenn, their abilities and character, magic and geography.',
                                          position='bright_town', role=role)
            self.assertLessEqual(len(check_json(packet)), BUDGETS[role])
            self.assertEqual(len(check_json(packet)), packet['search']['characters'])
            self.assertLessEqual(len(packet['facts']), LIMITS[role])
            self.assertLessEqual(packet['search']['candidates'], 96)
            self.assertLessEqual(packet['search']['requests'], 3)
            self.assertEqual(packet['search']['model_calls'], 0)

    def test_later_volume_and_specific_character_without_unrelated_filling(self):
        packet = self.selector.select('Tolven: abilities and character.')
        self.assertTrue(any(f['source']['volume'] == 5 for f in packet['facts']))
        self.assertTrue(all(f['subject'] == 'Tolven' or 'Tolven' in f['statement'] for f in packet['facts']))
        self.assertFalse(any(f['subject'] in ('Brenn', 'Ysolde') for f in packet['facts']))

    def test_aliases_resolve_and_faction_character_not_merged(self):
        subjects = self.selector.subjects(['Orrin Vale'])
        self.assertTrue(any(s['type'] == 'character' for s in subjects))
        self.assertTrue(any(s['type'] == 'faction' for s in subjects))
        self.assertGreaterEqual(len({s['id'] for s in subjects}), 2)
        subjects = self.selector.subjects(['Harrow'])
        self.assertTrue(any(s['name'] == 'Harrow Village' for s in subjects))

    def test_missing_subject_returns_no_filler(self):
        packet = self.selector.select('I speak to Zorbulaflix.', 'A table in a house.', 'original_place')
        self.assertEqual(packet['facts'], [])

    def test_existing_preparation_deduplicates_source_ids(self):
        packet = self.selector.select('What are the ranks of the Guild?',
                                      existing_text='Source already provided: ' + GUILD_RANKS + '.')
        self.assertNotIn('canon:' + GUILD_RANKS, [f['reference'] for f in packet['facts']])

    def test_exact_reference_and_unknown_date_preserved(self):
        packet = self.selector.select(query='canon:' + EXACT)
        item = next(f for f in packet['facts'] if f['reference'] == 'canon:' + EXACT)
        self.assertEqual(item['source']['volume'], 5)
        self.assertIsNone(item['date_source'])
        self.assertFalse(item['player_knowledge'])

    def test_episode_does_not_become_campaign_fact_or_player_memory(self):
        before = self.c.state()
        journal = self.c.journal()
        prepared = self.c.prepare_turn('I go to Harrow.', narrative_memory=True)
        self.assertTrue(any(f['usage'] == 'novel_episode' for f in prepared['documentary_canon']['facts']))
        self.assertEqual(self.c.state(), before)
        self.assertEqual(self.c.journal(), journal)
        self.assertFalse(any(p['reference'].startswith('canon:') for p in self.c.memories('Harrow')['proofs']))

    def test_private_sources_excluded_from_public_context_and_planner_summaries(self):
        prepared = self.c.prepare_turn('Tolven: abilities.', narrative_memory=True)
        with self.c.transaction() as con:
            WorldPlanner(con).initialize()
        # The summarizer no longer receives the canon: its public summaries have no canonical source.
        with self.c.transaction() as con:
            packet = WorldPlanner(con).packet(prepared, ['periodic_summary'])
        self.assertNotIn('documentary_canon', packet)
        self.assertFalse(any(r.startswith('canon:') for r in packet['allowed_references']))
        public = ContextBuilder(self.c).build('player', query='Tolven')
        self.assertFalse(any(f['origin'] == 'canon' for f in public['information']))

    def test_planner_uses_project_actors_without_exposing_them_to_narrator(self):
        state, config = self.c.state(), self.c.config()
        narrator = select_for_turn(state, config, 'Hello.', 'A table.', '', role='narrator')
        planner = select_for_turn(state, config, 'Hello.', 'A table.', '',
            projects=[{'actor': 'Tolven', 'location': 'place to define', 'title': 'Prepare a contact'}], role='planner')
        self.assertTrue(any(f['subject'] == 'Tolven' for f in planner['facts']))
        self.assertFalse(any(f['subject'] == 'Tolven' for f in narrator['facts']))

    def test_canon_only_cause_rejected_but_local_action_with_source_accepted(self):
        prepared = self.c.prepare_turn('I go to Harrow.', narrative_memory=True)
        ref = prepared['documentary_canon']['facts'][0]['reference']
        state = prepared['state']
        e = event(state, 'The character notices a detail.',
                  [{'operation': 'set', 'path': '/intentions/player', 'value': 'Look around.'}], causes=[ref])
        p = proposal(state, 'You look around.', [e], request_id=prepared['model_proposal']['request_id'])
        result = self.c.apply(p)
        self.assertEqual(result['status'], 'rejected')
        self.assertIn('local cause', result['reason'])
        fresh = self.c.prepare_turn(reevaluate=True, narrative_memory=True)
        e['event_id'] = 'new_event'
        e['causes'] = [ref, 'action:' + fresh['model_proposal']['request_id']]
        p = proposal(fresh['state'], 'You look around.', [e], request_id=fresh['model_proposal']['request_id'])
        self.assertEqual(self.c.apply(p)['status'], 'recorded')

    def test_unknown_canonical_reference_is_rejected(self):
        prepared = self.c.prepare_turn('Hello.', narrative_memory=True)
        state = prepared['state']
        rid = prepared['model_proposal']['request_id']
        e = event(state, 'A detail is noticed.', [{'operation': 'set', 'path': '/intentions/player', 'value': 'Observe.'}],
                  causes=['canon:invented_reference', 'action:' + rid])
        result = self.c.apply(proposal(state, 'You observe.', [e], request_id=rid))
        self.assertEqual(result['status'], 'rejected')

    def test_canonical_origin_remains_private_in_agenda(self):
        prepared = self.c.prepare_turn('Tolven.', narrative_memory=True)
        ref = prepared['documentary_canon']['facts'][0]['reference']
        entry = {'entry_id': 'test:secret', 'expected_revision': -1, 'title': 'Private preparation', 'actor': 'Tolven',
                 'location': 'place', 'kind': 'project', 'due_minute': 5, 'conditions': [], 'references': [ref],
                 'audience': ['narrator', 'player'], 'interrupt': True, 'status': 'planned',
                 'reason': 'Preparation.', 'outcome_event_index': None}
        with self.c.transaction() as con:
            with self.assertRaisesRegex(ValueError, 'private source'):
                Agenda(con).validate(prepared['model_proposal'], [entry], None, [], {ref}, prepared['state'],
                                     prepared['world_agenda'])

    def test_saved_proposal_reuses_canonical_sources_after_crash(self):
        narrator = FakeNarrator()

        def response(packet, result):
            ref = packet['documentary_canon']['facts'][0]['reference']
            result['events'] = [{'world_minute': 0, 'description': 'The character examines the situation.',
                'causes': [ref, 'current_action'], 'conditions': [],
                'changes': [{'operation': 'set', 'path': '/intentions/player', 'value_json': '"Examine."'}],
                'audience': ['narrator', 'player']}]
            return result
        narrator.transform = response
        loop = NarrativeLoop(self.c, narrator)
        with patch.object(self.c, 'apply', side_effect=OSError('Crash before application')):
            with self.assertRaises(OSError):
                loop.run_turn('I go to Harrow.')
        result = loop.run_turn(resume=True)
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(narrator.calls, 1)
        self.assertEqual(loop.last_metrics['model_calls'], 0)

    def test_preparation_does_not_accumulate_duplicate_contexts(self):
        for i in range(3):
            self.c.prepare_turn('Tolven.' if i == 0 else None, narrative_memory=True)
        with self.c.transaction() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM canon_selection_contexts').fetchone()[0], 1)

    def test_source_snapshot_stays_read_only(self):
        before = hashlib.sha256(self.snapshot.read_bytes()).hexdigest()
        self.selector.select('Tolven: abilities.', role='planner')
        self.assertEqual(hashlib.sha256(self.snapshot.read_bytes()).hexdigest(), before)

    def test_incompatible_snapshot_revision_is_refused(self):
        state = self.c.state()
        state['versions']['canon_revision'] = 126
        with self.assertRaisesRegex(ValueError, 'incompatible'):
            CanonSelector(state, self.c.config())

    def test_canonical_selection_not_exposed_as_player_role(self):
        with self.assertRaises(ValueError):
            self.selector.select('Tolven.', role='player')

    def test_receipt_from_another_request_does_not_authorize_a_source(self):
        prepared = self.c.prepare_turn('Tolven.', narrative_memory=True)
        ref = prepared['documentary_canon']['facts'][0]['reference']
        state = self.c.state()
        # Simulates an independent manual application, with no context supplied for this request_id.
        e = event(state, 'The character observes.', [{'operation': 'set', 'path': '/intentions/player', 'value': 'Observe.'}],
                  causes=[ref, 'action:other_request'])
        result = self.c.apply(proposal(state, 'You observe.', [e], request_id='other_request'), background=True)
        self.assertEqual(result['status'], 'rejected')
        self.assertIn('missing', result['reason'])

    def test_keywords_do_not_override_a_specific_subject_for_narrator(self):
        packet = self.selector.select('Tolven, his abilities.', 'Brenn and Ysolde are talking.', 'bright_town')
        self.assertTrue(packet['facts'])
        self.assertTrue(all(f['subject'] == 'Tolven' or 'Tolven' in f['statement'] for f in packet['facts']))

    def test_keyword_focus_does_not_fill_with_unrelated_city_facts(self):
        packet = self.selector.select('I leave with the grain carts.', position='grain_cart_yard_bright_town')
        self.assertTrue(packet['facts'])
        for item in packet['facts']:
            self.assertTrue(any(t in item['statement'].lower() for t in ('grain', 'cart', 'transport', 'merchant', 'escort')),
                            item['statement'])

    def test_canonical_memory_request_recovers_locally_and_keeps_action(self):
        self.c.begin_action('I ask for a clarification.')
        before = self.c.state()
        scene = self.c.resume()['session']['scene']
        request = self.c.resume()['session']['pending_action']['request_id']
        result = self.c.recover_memory(request, 'canon:' + EXACT)
        self.assertEqual(result['search_status'], 'ready')
        self.assertEqual(self.c.state(), before)
        self.assertEqual(self.c.resume()['session']['scene'], scene)
        prepared = self.c.prepare_turn(narrative_memory=True)
        self.assertIn('canon:' + EXACT, [f['reference'] for f in prepared['documentary_canon']['facts']])

    def test_changed_versions_after_generation_require_explicit_retry(self):
        narrator = FakeNarrator()
        loop = NarrativeLoop(self.c, narrator)
        with patch.object(self.c, 'apply', side_effect=OSError('Crash before application')):
            with self.assertRaises(OSError):
                loop.run_turn('Tolven: abilities.')
        with self.c.transaction() as con:
            state = self.c._state(con)
            state['versions']['adaptation_version'] = 'new_revision'
            con.execute('UPDATE campaign SET state=?', (check_json(state),))
        with self.assertRaisesRegex(ModelUnavailable, 'modified'):
            loop.run_turn(resume=True)
        self.assertEqual(narrator.calls, 1)
        self.assertEqual(loop.run_turn(resume=True, retry=True)['status'], 'recorded')
        self.assertEqual(narrator.calls, 2)

    def test_application_checks_versions_even_without_events(self):
        prepared = self.c.prepare_turn('Hello.', narrative_memory=True)
        p = prepared['model_proposal']
        p['narration'] = 'The guard greets you.'
        old = deepcopy(prepared['state']['versions'])
        with self.c.transaction() as con:
            state = self.c._state(con)
            state['versions']['adaptation_version'] = 'modified'
            con.execute('UPDATE campaign SET state=?', (check_json(state),))
        with self.assertRaisesRegex(ValueError, 'Documentary versions'):
            self.c.apply(p, expected_versions=old)
        self.assertIsNone(self.c.result(p['request_id']))

    def test_schema_does_not_offer_documentary_sources_for_memory_updates(self):
        schema = response_schema(references=['canon:' + EXACT, 'plan:contact:0', 'exchange:1'])
        refs = schema['properties']['memory_updates']['items']['properties']['references']['items']['enum']
        self.assertEqual(refs, ['current_turn', 'exchange:1'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
