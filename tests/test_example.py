"""The bundled example novel installs end to end, with no account and no model."""
import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from novel_engine import cli, resume
from novel_engine.contracts import ROOT
from novel_engine.memory import CanonRepository
from novel_engine.reader import Reader

ANSWER = json.loads((cli.EXAMPLE / 'canon_answer.json').read_text(encoding='utf-8'))


class ExampleNovelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='.tmp-example-', dir=ROOT))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.slug = 'exampletest'
        self.target = ROOT / 'campaigns' / self.slug
        self.addCleanup(shutil.rmtree, self.target, True)
        self.active = resume.ACTIVE
        self.before = self.active.read_text(encoding='utf-8') if self.active.is_file() else None
        self.addCleanup(self.restore_active)

    def restore_active(self):
        if self.before is None:
            self.active.unlink(missing_ok=True)
        else:
            self.active.write_text(self.before, encoding='utf-8')

    def install(self):
        with mock.patch.object(cli, 'NOVELS', self.tmp):
            cli.main(['example', '--slug', self.slug])

    def test_every_prepared_claim_is_found_in_the_story_and_stored_with_its_quote(self):
        self.install()
        canon = self.tmp / self.slug / 'canon.sqlite'
        with closing(sqlite3.connect(canon)) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM raw_claims').fetchone()[0], len(ANSWER['claims']))
            self.assertEqual(con.execute('SELECT COUNT(*) FROM sources WHERE quote IS NOT NULL').fetchone()[0],
                             len(ANSWER['claims']))
            self.assertEqual(con.execute("SELECT value FROM metadata WHERE key='volume_1'").fetchone()[0], 'closed')
        repo = CanonRepository(canon, 1)
        self.assertTrue(repo.search('harbor fund iron chest'))

    def test_campaign_carries_the_pack_rules_and_presentation(self):
        self.install()
        _, campaign = resume.active_campaign()
        config = campaign.config()
        self.assertEqual(config['player_name'], 'Wren Ashby')
        from novel_engine.narrative import NarrativeLoop
        prompt = NarrativeLoop(campaign, None).build_prompt(campaign.prepare_turn('I look around.'))
        self.assertIn('Seventeen, apprentice ferry pilot of Saltmere', prompt)
        self.assertEqual([n['id'] for n in config['tracked_npcs']], ['maris_thorne', 'corwen_pike'])
        self.assertEqual(len(config['world_rules']), 2)
        self.assertEqual(config['possession_rules']['currency_rates'], {'gold': {'silver': 20}})
        reader = Reader(self.target / 'campaign.sqlite').config()
        self.assertEqual((reader['title'], reader['labels']['tab.npc'], reader['labels']['tab.story']),
                         ('The Salt Lantern', 'People', 'Story'))
        agenda = campaign.world_agenda('narrator')
        self.assertIn('npc_step:maris_thorne', [e['entry_id'] for e in agenda['events']])

    def test_opening_scene_declared_powers_and_a_manual_turn_with_character_rules(self):
        import contextlib
        import io
        from novel_engine.scripts.play_narrative import main
        self.install()
        reader = Reader(self.target / 'campaign.sqlite')
        self.assertTrue(reader.state()['scene'].startswith('The Storm Festival fills the Saltmere quay'))
        names = {item['id']: item for item in reader.abilities()['abilities']}
        self.assertEqual(sorted(names), ['knot_work', 'second_wind', 'tide_sense'])
        self.assertEqual((names['tide_sense']['default_hp_cost'], names['second_wind']['full_power_hp_cost']), (0, 9))
        from novel_engine.adapters import ModelUnavailable
        # The manual engine waits for an answer: the request was prepared with the character's abilities.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()),                 self.assertRaisesRegex(ModelUnavailable, 'Manual engine'):
            main(['--campaign', str(self.target / 'campaign.sqlite'), '--text', 'I take the oars.',
                  '--engine', 'manual', '--json'])
        request = (self.target / 'manual' / 'turn.prompt.md').read_text(encoding='utf-8')
        self.assertIn('tide_sense', request)
        self.assertIn('saltmere_quay', request)

    def answer_from_schema(self, narration):
        """A valid, empty-handed answer to the request the manual engine wrote, built from its JSON Schema."""
        import re
        manual = self.target / 'manual'
        request = (manual / 'turn.prompt.md').read_text(encoding='utf-8')
        schema = json.loads(re.search(r'```json\n(.*)\n```', request, re.S).group(1))
        answer = {}
        for key, spec in schema['properties'].items():
            kind = spec.get('type')
            if len(spec.get('enum', [])) == 1:
                answer[key] = spec['enum'][0]
            elif key == 'narration':
                answer[key] = narration
            elif kind == 'array' or (isinstance(kind, list) and 'array' in kind and key != 'memory_request'):
                answer[key] = []
            elif isinstance(kind, list) and 'null' in kind:
                answer[key] = None
        (manual / 'turn.response.json').write_text(json.dumps(answer), encoding='utf-8')

    def test_a_whole_manual_turn_is_recorded_and_shown_to_the_reader(self):
        import contextlib
        import io
        from novel_engine.adapters import ModelUnavailable
        from novel_engine.scripts.play_narrative import main
        self.install()
        save = str(self.target / 'campaign.sqlite')
        quiet = contextlib.redirect_stderr(io.StringIO())
        with quiet, contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ModelUnavailable):
            main(['--campaign', save, '--text', 'I take the oars.', '--engine', 'manual', '--json'])
        self.answer_from_schema('The Dunlin slides off the quay into the fog.')
        out = io.StringIO()
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(out):
            code = main(['--campaign', save, '--resume', '--retry', '--engine', 'manual', '--json'])
        result = json.loads(out.getvalue())
        self.assertEqual((code, result['status'], result['state_version_after']), (0, 'recorded', 1))
        story = Reader(self.target / 'campaign.sqlite').story()['exchanges']
        self.assertEqual([e['role'] for e in story], ['narrator', 'player', 'narrator'])
        self.assertEqual(story[-1]['content'], 'The Dunlin slides off the quay into the fog.')

    def test_installing_twice_under_the_same_name_is_refused(self):
        self.install()
        with self.assertRaises(SystemExit):
            self.install()

    def test_the_example_text_is_original_and_every_bundled_file_is_valid_json(self):
        for name in ('novel.json', 'character.json', 'theme.json', 'canon_answer.json'):
            json.loads((cli.EXAMPLE / name).read_text(encoding='utf-8'))
        self.assertGreater(len((cli.EXAMPLE / 'story.txt').read_text(encoding='utf-8')), 5000)


if __name__ == '__main__':
    unittest.main()
