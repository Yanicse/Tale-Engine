"""A complete sheet may keep unknown properties, with no new ability."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from novel_engine.campaign import Campaign
from novel_engine.demo import configuration, initial_state
from novel_engine.instructions import LOCAL_SHEETS
from novel_engine.narrative import NarrativeLoop


def item(quantity=1):
    return {'owner_id': 'player', 'quantity': quantity, 'equipped': False, 'description': 'A lot of wood.',
            'references': ['demo:initial_configuration'], 'audience': ['narrator', 'player'],
            'location': 'on:player', 'condition': 'Sound', 'quantity_precision': 'exact',
            'possession_status': 'owns', 'unit': 'piece'}


class LocalSheetTests(unittest.TestCase):
    def setUp(self):
        folder = ROOT / '.tmp-tests'
        folder.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=folder)
        state = initial_state()
        state['inventory'] = {f'object_{i}': item() for i in range(8)}
        state['inventory']['object_7']['description'] = ('According to the label, a passage to a known place. '
                                                         'Charges and passengers unknown.')
        self.c = Campaign(Path(self.temp.name) / 'sheet.sqlite').create(state, configuration())
        self.c.open_scene('An ordinary conversation.')
        self.rid = self.c.begin_action('thanks')['request_id']

    def tearDown(self):
        self.temp.cleanup()

    def test_recovered_sheet_is_exact_in_both_prompts_without_confirming_unknown_properties(self):
        before = deepcopy(self.c.state())
        self.assertEqual(self.c.recover_memory(self.rid, 'object:object_7')['search_status'], 'ready')
        prepared = self.c.prepare_turn(narrative_memory=True)
        for compact in (True, False):
            prompt = NarrativeLoop(self.c, None).build_prompt(prepared, compact=compact)
            self.assertIn(LOCAL_SHEETS, prompt)
            packet = json.loads(prompt.rsplit('\nCURRENT TURN\n', 1)[1])
            self.assertEqual(packet['current_state']['inventory']['object_7'], before['inventory']['object_7'])
            if compact:
                self.assertIn('object_7', packet['inventory_projection']['complete_sheets'])
        self.assertEqual(self.c.state(), before)

    def test_unrecovered_sheet_is_catalogued_with_its_exact_references(self):
        prepared = self.c.prepare_turn(narrative_memory=True)
        packet = json.loads(NarrativeLoop(self.c, None).build_prompt(prepared).rsplit('\nCURRENT TURN\n', 1)[1])
        self.assertNotIn('object_7', packet['current_state']['inventory'])
        entry = packet['object_catalogue']['object_7']
        self.assertEqual(entry['references'], ['demo:initial_configuration'])
        self.assertEqual(entry['audience'], ['narrator', 'player'])
        self.assertIn('object_7', packet['inventory_projection']['catalogued_sheets'])

    def test_repeated_sheet_request_never_resets_search_budget(self):
        self.c.recover_memory(self.rid, 'object:object_7')
        self.assertEqual(self.c.recover_memory(self.rid, 'object:object_7', reset=True)['search_status'], 'blocked')
        with closing(sqlite3.connect(self.c.path)) as con:
            self.assertEqual(con.execute('SELECT searches FROM action_recovery_budget WHERE request_id=?',
                                         (self.rid,)).fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
