"""Relations contract and instructions of both prompts, with no remote call."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from novel_engine.campaign import Campaign
from novel_engine.contracts import ROOT
from novel_engine.demo import configuration, event, initial_state, proposal
from novel_engine.narrative import NarrativeLoop
from novel_engine.instructions import RELATIONS


class RelationTests(unittest.TestCase):
    def setUp(self):
        folder = ROOT / '.tmp-tests'
        folder.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=folder)
        self.c = Campaign(Path(self.temp.name) / 'relations.sqlite').create(initial_state(), configuration())
        self.c.open_scene('Two characters discuss their relationship.')

    def tearDown(self):
        self.temp.cleanup()

    def test_both_prompts_include_the_relation_contract_even_for_short_dialogue(self):
        prepared = self.c.prepare_turn('oui', narrative_memory=True)
        for compact in (True, False):
            self.assertIn(RELATIONS, NarrativeLoop(self.c, None).build_prompt(prepared, compact=compact))

    def test_invalid_shape_is_refused_and_precise_event_survives_valid_shape(self):
        before = self.c.state()
        refs, audience = ['demo:initial_configuration'], ['narrator', 'player']
        detail = ('The two characters make their romantic relationship official without a cohabitation '
                  'agreement.')
        invalid = {'description': detail, 'status': 'romantic_relationship_made_official',
                   'references': refs, 'audience': audience}
        e = event(before, detail, [{'operation': 'set', 'path': '/relations/partner', 'value': invalid}])
        rejected = self.c.apply(proposal(before, events=[e]))
        self.assertEqual(rejected['status'], 'rejected')
        self.assertIn('missing fields', rejected['reason'])
        self.assertEqual(self.c.state(), before)
        valid = {'from_id': 'player', 'to_id': 'partner', 'attitude': 'friendly',
                 'references': refs, 'audience': audience}
        e = event(before, detail, [{'operation': 'set', 'path': '/relations/partner', 'value': valid}])
        p = proposal(before, narration=detail, events=[e])
        result = self.c.apply(p)
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(Campaign(self.c.path).state()['relations']['partner'], valid)
        self.assertEqual(result['applied_events'][0]['description'], detail)
        after = deepcopy(self.c.state())
        self.assertEqual(self.c.apply(p), result)
        self.assertEqual(self.c.state(), after)
