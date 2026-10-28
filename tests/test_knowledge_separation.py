"""Who may know what: beliefs, secrets, old promises and canon access, with a canon built from the bundled example."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from novel_engine import cli
from novel_engine.campaign import Campaign
from novel_engine.contracts import ROOT as HOME
from novel_engine.demo import action, configuration, event, initial_state, proposal
from novel_engine.memory import CanonRepository, ContextBuilder

SCHEMAS = ROOT / 'novel_engine' / 'assets' / 'docs' / 'engine' / 'contracts'


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix='.tmp-knowledge-', dir=HOME))
        cls.slug = 'knowledgetest'
        before = HOME / 'campaigns' / 'active.json'
        saved = before.read_text(encoding='utf-8') if before.is_file() else None
        with mock.patch.object(cli, 'NOVELS', cls.tmp):
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                cli.main(['example', '--slug', cls.slug])
        shutil.rmtree(HOME / 'campaigns' / cls.slug, True)
        if saved is None:
            before.unlink(missing_ok=True)
        else:
            before.write_text(saved, encoding='utf-8')
        cls.canon = cls.tmp / cls.slug / 'canon.sqlite'

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def setUp(self):
        base = HOME / '.tmp-tests'
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.state = initial_state()
        self.state['versions'].update(canon_revision=1, canon_snapshot=self.canon.relative_to(HOME).as_posix())
        self.c = Campaign(Path(self.temp.name) / 'campaign.sqlite').create(self.state, configuration())


class KnowledgeTests(Fixture):
    def test_a_belief_keeps_its_category_and_owner(self):
        state = self.c.state()
        belief = {'statement': 'The traveler would be a spy.', 'references': ['demo:arrival_event'], 'status': 'believed'}
        e = event(state, 'The lookout forms a belief.', [{'operation': 'set', 'path': '/beliefs/faction', 'value': [belief]}],
                  audience=['narrator', 'npc:faction'])
        self.assertEqual(self.c.apply(proposal(state, events=[e]))['status'], 'recorded')
        context = ContextBuilder(self.c).build('npc:faction', query='spy')
        self.assertTrue(any(i['category'] == 'belief' for i in context['information']))
        self.assertFalse(ContextBuilder(self.c).build('player', query='spy')['information'])

    def test_an_old_promise_stays_persistent_and_searchable_then_is_kept(self):
        state = self.c.state()
        promise = {'description': 'Promise to bring a letter to the merchant.', 'status': 'open',
                   'references': ['demo:arrival_event'], 'audience': ['narrator', 'player']}
        e = event(state, 'A promise is made.', [{'operation': 'set', 'path': '/commitments/letter', 'value': promise}])
        self.c.apply(proposal(state, events=[e]))
        for _ in range(3):
            self.c.apply(action(self.c.state(), 'wait'))
        context = ContextBuilder(Campaign(self.c.path)).build('player', query='promise letter')
        self.assertIn('letter', context['commitment_ids'])
        self.assertTrue(context['information'])
        state = self.c.state()
        promise['status'] = 'kept'
        self.c.apply(proposal(state, events=[event(state, 'Letter delivered.',
                                                  [{'operation': 'set', 'path': '/commitments/letter', 'value': promise}])]))
        self.assertEqual(self.c.state()['commitments']['letter']['status'], 'kept')

    def test_a_broken_promise_and_a_contradicted_belief_use_english_statuses(self):
        for name, path in (('commitments', ('commitments', 'additionalProperties', 'properties', 'status', 'enum')),):
            schema = json.loads((SCHEMAS / 'v0_2' / 'state.schema.json').read_text(encoding='utf-8'))
            node = schema['properties'][path[0]]
            for key in path[1:]:
                node = node[key]
            self.assertEqual(node, ['open', 'kept', 'broken'])
        schema = json.loads((SCHEMAS / 'v0_2' / 'state.schema.json').read_text(encoding='utf-8'))
        beliefs = schema['properties']['beliefs']['additionalProperties']['items']['properties']['status']['enum']
        self.assertEqual(beliefs, ['believed', 'contradicted'])

    def test_a_secret_event_and_effect_never_reach_the_player_context(self):
        state = self.c.state()
        secret = {'actor_id': 'player', 'description': 'Secret curse.', 'expires_at': None,
                  'references': ['demo:arrival_event'], 'audience': ['narrator']}
        e = event(state, 'The traveler is secretly cursed.', [{'operation': 'set', 'path': '/effects/curse', 'value': secret}],
                  audience=['narrator'])
        self.c.apply(proposal(state, events=[e]))
        self.assertFalse(any('secret' in i['text'].casefold() for i in ContextBuilder(self.c).build('player')['information']))
        self.assertTrue(ContextBuilder(self.c).build('narrator')['information'])


class CanonAccessTests(Fixture):
    def test_the_canon_is_searched_read_only_and_an_unvalidated_period_is_excluded(self):
        path = self.canon
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        rows = CanonRepository(path, 1).search('harbor fund', limit=3)
        self.assertTrue(rows)
        self.assertTrue(rows[0]['sources'])
        self.assertTrue(ContextBuilder(self.c).build('narrator', query='harbor fund')['unknowns'])
        self.assertFalse(ContextBuilder(self.c).build('player', query='harbor fund')['information'])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_a_claim_needs_a_temporal_validation_and_acquired_knowledge_to_reach_the_player(self):
        state = self.c.state()
        row = CanonRepository(self.canon, 1).search('harbor fund', 1)[0]
        config = configuration()
        config['canon_access'] = {row['claim_id']: {'validated_for_start': True, 'audience': ['narrator', 'player']}}
        other = Campaign(Path(self.temp.name) / 'other.sqlite').create(state, config)
        self.assertFalse(ContextBuilder(other).build('player', query='harbor fund')['information'])
        learned = event(state, 'The player learns a fact.',
                        [{'operation': 'set', 'path': '/knowledge/player', 'value': [row['claim_id']]}])
        other.apply(proposal(state, events=[learned]))
        self.assertTrue(any(i['origin'] == 'canon' for i in ContextBuilder(other).build('player', query='harbor fund')['information']))

    def test_a_wrong_canon_revision_fails(self):
        with self.assertRaises(ValueError):
            CanonRepository(self.canon, 77)

    def test_the_book_text_fallback_is_refused_with_a_clear_message_when_no_corpus_exists(self):
        with self.assertRaisesRegex(ValueError, 'No source corpus indexed'):
            CanonRepository(self.canon, 1).search_sources('harbor', limit=1)


if __name__ == '__main__':
    unittest.main()
