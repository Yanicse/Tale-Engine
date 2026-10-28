import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from novel_engine import cli, pack
from novel_engine.contracts import ROOT
from novel_engine.reader import Reader


class PackTest(unittest.TestCase):
    def test_theme_keeps_only_safe_values(self):
        theme = pack.clean_theme({
            'preset': 'nonsense', 'colors': {'accent': '#abc', 'text': 'red; background:url(x)', 'bogus': '#fff'},
            'labels': {'send': 'Go', 'tab.npc': 'People', 'evil': 'x'},
            'icons': {'objects': [['key|lock', 'broadsword'], ['<script>', 'broadsword'], ['ok', 'no-such-icon']],
                      'nowhere': [['a', 'broadsword']]}})
        self.assertEqual(theme['preset'], 'auto')
        self.assertEqual(theme['colors'], {'accent': '#abc'})
        self.assertEqual(theme['labels'], {'send': 'Go', 'tab.npc': 'People'})
        self.assertEqual(theme['icons'], {'objects': [['key|lock', 'broadsword']]})

    def test_tracked_npcs_are_normalized(self):
        npcs = pack.clean_npcs([{'name': 'Old Mira', 'goal': 'sail'}, {'id': 'x'}, 'bad'])
        self.assertEqual([n['id'] for n in npcs], ['old_mira'])

    def test_theme_color_keys_are_english(self):
        theme = pack.clean_theme({'colors': {'muted': '#111', 'line': '#222', 'card': '#333', 'gold': '#444',
                                             'doux': '#555', 'carte': '#666'}})
        self.assertEqual(set(theme['colors']), {'muted', 'line', 'card', 'gold'})

    def test_character_rules_receive_the_sheet_hp_max(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            (Path(tmp) / 'novel.json').write_text('{"title": "T"}', encoding='utf-8')
            (Path(tmp) / 'character.json').write_text(
                '{"name": "Mira", "hp_max": 80, "rules": {"forbidden_resources": ["x"]}}', encoding='utf-8')
            config = pack.campaign_config(tmp, {})
        self.assertEqual(config['character_rules'], {'hp_max': 80, 'forbidden_resources': ['x']})

    def test_missing_novel_json_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            pack.campaign_config(tmp, {})


class PackCampaignTest(unittest.TestCase):
    def test_pack_drives_reader_config_and_agenda(self):
        from novel_engine import canon_build, resume
        tmp = Path(tempfile.mkdtemp(prefix='.tmp-pack-', dir=ROOT))
        self.addCleanup(shutil.rmtree, tmp, True)
        with mock.patch.object(cli, 'NOVELS', tmp):
            cli.main(['init', 'packtest', '--preset', 'scifi'])
            folder = tmp / 'packtest'
            novel = json.loads((folder / 'novel.json').read_text(encoding='utf-8'))
            novel.update(language='fr', tracked_npcs=[{'name': 'Captain Vale', 'goal': 'find the map'}],
                         world_rules=['Magic costs a day of rest.'], currency_rates={'gold': {'silver': 100}})
            (folder / 'novel.json').write_text(json.dumps(novel), encoding='utf-8')
            theme = json.loads((folder / 'theme.json').read_text(encoding='utf-8'))
            theme.update(title='The Vale Logs', subtitle='Frontier', labels={'send': 'Envoyer'}, clock=False)
            (folder / 'theme.json').write_text(json.dumps(theme), encoding='utf-8')
            book = tmp / 'b.txt'
            book.write_text('Captain Vale seeks a map.', encoding='utf-8')
            cli.main(['ingest', str(book), '--novel', 'packtest'])
            batch = canon_build.plan(folder)[0]
            (folder / 'work' / (batch['id'] + '.response.json')).write_text(json.dumps({
                'entities': [{'id': 'vale', 'kind': 'character', 'name': 'Vale'}],
                'claims': [{'subject': 'vale', 'statement': 'Vale seeks a map.', 'quote': 'Vale seeks a map', 'page': 1}]}), encoding='utf-8')
            canon_build.build(folder, 'manual', log=lambda *_: None)
            cli.main(['character', 'new', 'packtest', '--name', 'Ana', '--hp', '40'])
            active = resume.ACTIVE
            before = active.read_text(encoding='utf-8') if active.is_file() else None
            target = ROOT / 'campaigns' / 'packtest'
            try:
                cli.main(['campaign', 'new', 'packtest'])
                save = target / 'campaign.sqlite'
                config = Reader(save).config()
                self.assertEqual((config['character'], config['language'], config['preset'], config['title'],
                                  config['clock'], config['labels']['send'], config['labels']['tab.story'],
                                  config['ui_language']),
                                 ('Ana', 'fr', 'scifi', 'The Vale Logs', False, 'Envoyer', 'Histoire', 'fr'))
                _, campaign = resume.active_campaign()
                agenda = campaign.world_agenda('narrator')
                self.assertIn('npc_step:captain_vale', [e['entry_id'] for e in agenda['events']])
                self.assertEqual(campaign.config()['world_rules'], ['Magic costs a day of rest.'])
            finally:
                if before is None:
                    active.unlink(missing_ok=True)
                else:
                    active.write_text(before, encoding='utf-8')
                shutil.rmtree(target, True)


if __name__ == '__main__':
    unittest.main()
