"""`novel validate`, the character assistant, abilities declared by a pack, and the opening scene."""
import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from novel_engine import cli, pack
from novel_engine.character import ability_context
from novel_engine.contracts import ROOT
from novel_engine.pack_check import check_pack


def write(folder, name, value):
    (Path(folder) / name).write_text(json.dumps(value), encoding='utf-8')


class PackFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='.tmp-check-', dir=ROOT))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.dir = self.tmp / 'demo'
        self.dir.mkdir()
        write(self.dir, 'novel.json', {'title': 'Demo', 'language': 'en'})

    def report(self, **files):
        for name, value in files.items():
            write(self.dir, name.replace('_json', '.json'), value)
        return check_pack(self.dir)

    def assertError(self, report, text):
        self.assertTrue(any(text in line for line in report.errors), (text, report.errors))

    def assertWarning(self, report, text):
        self.assertTrue(any(text in line for line in report.warnings), (text, report.warnings))


class ValidateTest(PackFiles):
    def test_the_bundled_example_has_no_error_and_no_warning_about_its_files(self):
        report = check_pack(cli.EXAMPLE)
        self.assertEqual(report.errors, [])

    def test_missing_and_broken_files_are_reported(self):
        (self.dir / 'novel.json').unlink()
        self.assertError(check_pack(self.dir), 'novel.json: file is missing')
        (self.dir / 'novel.json').write_text('{oops', encoding='utf-8')
        self.assertError(check_pack(self.dir), 'not valid JSON')
        (self.dir / 'novel.json').write_text('[]', encoding='utf-8')
        self.assertError(check_pack(self.dir), 'must contain a JSON object')
        self.assertError(check_pack(self.tmp / 'nothing'), 'no such novel pack folder')

    def test_theme_problems_are_listed_one_by_one(self):
        report = self.report(theme_json={'preset': 'neon', 'colors': {'accent': 'red', 'sparkle': '#fff'},
                                         'labels': {'tab.npc': 'People', 'nope': 'x', 'search.results': 'none'},
                                         'icons': {'places': [['harbor', 'no-such-icon']], 'weather': []},
                                         'clock': 'yes', 'mood': 1})
        self.assertError(report, "preset 'neon'")
        self.assertError(report, 'colors.accent')
        self.assertError(report, 'colors.sparkle')
        self.assertError(report, 'labels.nope')
        self.assertError(report, "icon 'no-such-icon'")
        self.assertError(report, 'icons.weather')
        self.assertError(report, "'clock'")
        self.assertWarning(report, "unknown key 'mood'")
        self.assertWarning(report, 'labels.search.results should keep the placeholders')

    def test_novel_problems(self):
        report = self.report(novel_json={'title': 'T', 'language': 'xx', 'tracked_npcs': [{'goal': 'x'}, 'bad', {'name': 'A'}, {'name': 'A'}],
                                         'currency_rates': {'gold': {'silver': 0}}, 'world_rules': [3],
                                         'opening': {'place': 'Quay'}, 'extra': 1})
        self.assertError(report, 'tracked_npcs[0] needs a text "name"')
        self.assertError(report, 'tracked_npcs[1] needs a text "name"')
        self.assertError(report, "duplicate id 'a'")
        self.assertError(report, 'currency_rates.gold')
        self.assertError(report, "'world_rules' must be a list of sentences")
        self.assertError(report, "'opening' needs a text 'place' and a text 'scene'")
        self.assertWarning(report, "language 'xx' has no interface pack")
        self.assertWarning(report, "unknown key 'extra'")
        self.assertWarning(report, 'has no goal')

    def test_character_rules_problems(self):
        report = self.report(character_json={'name': 'Ana', 'hp_max': 0, 'rules': {
            'charges': {}, 'regeneration': {}, 'speed': 1,
            'abilities': {'Bad Id': {'label': ''}, 'bolt': {'cooldown': {'resource': 'x'}, 'free': 'no'}},
            'costs': {'full_power_cost_percent': {'bolt': 120, 'ghost': 5}, 'default_output_percent': 0,
                      'free_output_up_to_percent': {'nobody': 10}},
            'forbidden_resources': [1], 'hunger_from_health': 'maybe'}})
        for text in ("'hp_max'", 'rules.regeneration', "rules.speed: unknown rule",
                     'rules.abilities.Bad Id: the id may only use', 'rules.abilities.Bad Id.label', 'cooldown needs a non-reserved',
                     'rules.abilities.bolt.free', 'full_power_cost_percent.bolt must be between 0 and 100',
                     'full_power_cost_percent.ghost: no such ability', 'default_output_percent must be a number above 0',
                     'free_output_up_to_percent.nobody', 'rules.forbidden_resources', 'rules.hunger_from_health'):
            self.assertError(report, text)

    def test_abilities_without_costs_get_a_warning_and_costs_need_a_table(self):
        report = self.report(character_json={'name': 'Ana', 'rules': {'abilities': {'bolt': {'label': 'Bolt'}}}})
        self.assertError(report, 'declare a mana cost or free: true')
        report = self.report(character_json={'name': 'Ana', 'rules': {'abilities': {'bolt': {'label': 'Bolt'}}, 'costs': {}}})
        self.assertError(report, 'full_power_cost_percent is required')

    def test_unbuilt_pack_gets_hints_not_errors(self):
        report = check_pack(self.dir)
        self.assertEqual(report.errors, [])
        self.assertWarning(report, 'no ingested book yet')
        self.assertWarning(report, 'not built yet')


class CommandsTest(PackFiles):
    def run_cli(self, *argv):
        out = io.StringIO()
        with mock.patch.object(cli, 'NOVELS', self.tmp), contextlib.redirect_stdout(out):
            try:
                code = cli.main(list(argv))
            except SystemExit as stop:
                code = stop.code
        return code, out.getvalue()

    def test_validate_exit_codes_and_output(self):
        code, output = self.run_cli('validate', 'demo')
        self.assertEqual(code, 0)
        self.assertIn('no error', output)
        write(self.dir, 'theme.json', {'preset': 'neon'})
        code, output = self.run_cli('validate')
        self.assertEqual(code, 1)
        self.assertIn('ERROR   theme.json', output)

    def test_campaign_new_refuses_a_pack_with_errors(self):
        write(self.dir, 'theme.json', {'preset': 'neon'})
        (self.dir / 'canon.sqlite').write_bytes(b'')
        code, output = self.run_cli('campaign', 'new', 'demo')
        self.assertIn('Fix the errors above first', str(code))
        self.assertFalse((ROOT / 'campaigns' / 'demo').exists())

    def test_the_assistant_builds_rules_from_ability_specs(self):
        self.assertEqual(cli.parse_ability('Tide Sense:Tide sense:0'), ('tide_sense', 'Tide sense', 0))
        self.assertEqual(cli.parse_ability('wind:Wind'), ('wind', 'Wind', None))
        self.assertEqual(cli.parse_ability('gale:Gale:12.5'), ('gale', 'Gale', 12.5))
        for bad in ('x', ':Label', 'a:b:c', 'a:b:101', 'a:b:1:2'):
            with self.assertRaises(SystemExit):
                cli.parse_ability(bad)
        rules = cli.ability_rules(['tide:Tide sense:0', 'gale:Gale:30', 'plain:Plain'])
        self.assertEqual(rules['abilities']['tide'], {'label': 'Tide sense', 'phase': 'always', 'free': True})
        self.assertEqual(rules['costs'], {'default_output_percent': 100, 'full_power_cost_percent': {'tide': 0, 'gale': 30}})
        self.assertEqual(cli.ability_rules([]), {})
        write(self.dir, 'character.json', {'name': 'Ana', 'hp_max': 40, 'rules': rules})
        self.assertEqual(check_pack(self.dir).errors, [])

    def test_character_command_writes_abilities_and_the_result_validates(self):
        code, _ = self.run_cli('character', 'new', 'demo', '--name', 'Ana', '--hp', '40', '--ability', 'gale:Gale:30')
        self.assertEqual(code, 0)
        sheet = json.loads((self.dir / 'character.json').read_text(encoding='utf-8'))
        self.assertEqual(sheet['rules']['costs']['full_power_cost_percent'], {'gale': 30})
        self.assertEqual(check_pack(self.dir).errors, [])


class RulesInTheCampaign(PackFiles):
    SHEET = {'name': 'Ana', 'hp_max': 40, 'rules': {
        'passives': {'eyes': {'label': 'Keen eyes', 'free': True}},
        'abilities': {'gale': {'label': 'Gale', 'description': 'Calls a wind.', 'limits': 'Open water only.'}},
        'costs': {'default_output_percent': 20, 'full_power_cost_percent': {'gale': 30}},
        'forbidden_resources': ['hunger']}}

    def test_costs_become_the_cost_table_and_the_rest_the_character_rules(self):
        write(self.dir, 'character.json', self.SHEET)
        config = pack.campaign_config(self.dir, {})
        self.assertEqual(config['spell_cost_rules'], {'default_output_percent': 20,
                                                      'full_power_cost_percent': {'gale': 30}, 'free_output_up_to_percent': {}})
        self.assertNotIn('costs', config['character_rules'])
        self.assertEqual(config['character_rules']['forbidden_resources'], ['hunger'])

    def test_declared_powers_are_seeded(self):
        powers = pack.seed_powers(self.SHEET)
        self.assertEqual(sorted(powers), ['eyes', 'gale'])
        self.assertEqual(powers['gale']['description'], 'Calls a wind.')
        self.assertEqual(powers['gale']['limits'], 'Open water only.')
        self.assertEqual(powers['eyes']['limits'], 'As described on the character sheet.')
        self.assertEqual(pack.seed_powers({}), {})

    def test_the_narrator_context_works_with_partial_rules(self):
        state = {'resources': {'player_hp': 30, 'player_hp_max': 40}, 'world_minute': 0}
        context = ability_context(state, {'abilities': {'gale': {'label': 'Gale'}}}, None)
        self.assertEqual((context['active_abilities'], context['default_power_pct'], context['max_hp']), (['gale'], 100, 40))
        self.assertNotIn('form', context)
        self.assertNotIn('regeneration_rules', context)
        self.assertNotIn('subjective_hunger', context)


if __name__ == '__main__':
    unittest.main()
