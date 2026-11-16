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
from home import isolated_home
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
                                         'clock': 'yes', 'opening_minute_of_day': 1440, 'mood': 1})
        self.assertError(report, "preset 'neon'")
        self.assertError(report, 'colors.accent')
        self.assertError(report, 'colors.sparkle')
        self.assertError(report, 'labels.nope')
        self.assertError(report, "icon 'no-such-icon'")
        self.assertError(report, 'icons.weather')
        self.assertError(report, "'clock'")
        self.assertError(report, "'opening_minute_of_day'")
        self.assertWarning(report, "unknown key 'mood'")
        self.assertWarning(report, 'labels.search.results should keep the placeholders')

    def test_novel_problems(self):
        report = self.report(novel_json={'title': 'T', 'language': 'xx', 'tracked_npcs': [{'goal': 'x'}, 'bad', {'name': 'A'}, {'name': 'A'}],
                                         'currency_rates': {'gold': {'silver': 0}}, 'world_rules': [3], 'quiet_news': {'interval_minutes': 5, 'urgent_every': -1, 'mood': 1},
                                         'opening': {'place': 'Quay'}, 'extra': 1})
        self.assertError(report, 'tracked_npcs[0] needs "name" as text')
        self.assertError(report, 'tracked_npcs[1] needs "name" as text')
        self.assertError(report, "duplicate id 'a'")
        self.assertError(report, 'currency_rates.gold')
        self.assertError(report, "'world_rules' must be a list of sentences")
        self.assertError(report, 'quiet_news.interval_minutes')
        self.assertError(report, 'quiet_news.urgent_every')
        self.assertError(report, "'opening' needs 'place' and 'scene' as text")
        self.assertWarning(report, "language 'xx' has no interface pack")
        self.assertWarning(report, "unknown key 'extra'")
        self.assertWarning(report, 'has no goal')

    def test_the_missing_title_warning_says_what_the_page_does(self):
        report = self.report(novel_json={'language': 'en'})
        self.assertWarning(report, "no 'title': the reader title comes from theme.json")
        self.assertFalse(any('folder name' in line for line in report.warnings))

    def test_tracked_npc_fields_are_checked_like_the_campaign_cleans_them(self):
        report = self.report(novel_json={'title': 'T', 'tracked_npcs': [
            {'id': 5, 'name': 'Ana', 'goal': 'x'}, {'name': 'Bo', 'place': 3, 'goal': ['x']}]})
        self.assertError(report, 'tracked_npcs[0].id must be text')
        self.assertError(report, 'tracked_npcs[1].place must be text')
        self.assertError(report, 'tracked_npcs[1].goal must be text')
        long_name = 'A name that goes on and on until the sixtieth character is long gone, then '
        report = self.report(novel_json={'title': 'T', 'tracked_npcs': [
            {'name': long_name + 'one', 'goal': 'x'}, {'name': long_name + 'two', 'goal': 'x'},
            {'id': 'Ana B', 'name': 'First', 'goal': 'x'}, {'id': 'ana-b', 'name': 'Second', 'goal': 'x'}]})
        self.assertEqual(len([line for line in report.errors if 'duplicate id' in line]), 2, report.errors)
        self.assertEqual(report.errors.count("novel.json: tracked_npcs[1]: duplicate id '" + pack.npc_id({'name': long_name}) + "'"), 1)
        write(self.dir, 'novel.json', {'title': 'T', 'tracked_npcs': [{'name': long_name + 'one'}, {'name': 'Bo', 'goal': 'x'}]})
        names = [npc['id'] for npc in pack.clean_npcs(json.loads((self.dir / 'novel.json').read_text())['tracked_npcs'])]
        self.assertEqual(names, [pack.npc_id({'name': long_name}), 'bo'])

    def test_passives_cannot_declare_what_only_abilities_honour(self):
        report = self.report(character_json={'name': 'Ana', 'rules': {'passives': {
            'eyes': {'label': 'Eyes', 'free': True, 'cooldown': {'resource': 'eyes_ready', 'seconds': 60}},
            'ward': {'label': 'Ward', 'free': True, 'effect': 'death_prevention',
                     'parameters': {'duration_seconds': 5, 'lethal_prevention_capacity_hp_max_percent': 10,
                                    'survival_hp_max_percent': 10}}}}})
        self.assertError(report, 'rules.passives.eyes.cooldown: only works on abilities')
        self.assertError(report, 'rules.passives.ward.effect: only works on abilities')
        report = self.report(character_json={'name': 'Ana', 'rules': {'passives': {
            'dusk': {'label': 'Dusk', 'free': True, 'phase': 'night'}}}})
        self.assertError(report, 'rules.passives.dusk.phase requires solar_schedule')
        report = self.report(character_json={'name': 'Ana', 'rules': {'passives': {
            'dusk': {'label': 'Dusk', 'free': True, 'phase': 'night'}},
            'solar_schedule': {'first_sunrise_minute': 360, 'first_sunset_minute': 1080}}})
        self.assertEqual(report.errors, [])

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


class ThemeOpeningMinuteCheckTests(PackFiles):
    def test_values_that_are_not_a_minute_of_the_day_are_errors(self):
        for value in (None, 1440, -1, 1.5, '480', True, False, [480]):
            with self.subTest(value=value):
                self.assertError(self.report(theme_json={'opening_minute_of_day': value}), "'opening_minute_of_day'")

    def test_the_first_and_last_minutes_of_the_day_and_an_absent_key_pass(self):
        for value in (0, 480, 1439):
            with self.subTest(value=value):
                self.assertEqual(self.report(theme_json={'opening_minute_of_day': value}).errors, [])
        self.assertEqual(self.report(theme_json={}).errors, [])


class QuietNewsCheckTests(PackFiles):
    def news(self, value):
        return self.report(novel_json={'title': 'Demo', 'language': 'en', 'quiet_news': value})

    def assertNoNewsComplaint(self, report):
        self.assertEqual([line for line in report.errors + report.warnings if 'quiet_news' in line], [])

    def test_a_valid_option_has_no_error_and_no_warning(self):
        report = self.news({'interval_minutes': 360, 'urgent_every': 0, 'instruction': 'Prefer harbor news.'})
        self.assertNoNewsComplaint(report)
        for interval in (30, 43200):
            self.assertEqual(self.news({'interval_minutes': interval}).errors, [])
        for every in (0, 100):
            self.assertEqual(self.news({'interval_minutes': 60, 'urgent_every': every}).errors, [])

    def test_the_option_must_be_an_object_with_an_interval(self):
        self.assertError(self.news([]), "'quiet_news' must be an object")
        for value in ({}, {'urgent_every': 3}):
            with self.subTest(value=value):
                self.assertError(self.news(value), 'quiet_news.interval_minutes')

    def test_the_interval_must_be_a_whole_number_inside_the_bounds(self):
        for value in (True, 29, 43201, 60.5, '60', None):
            with self.subTest(value=value):
                self.assertError(self.news({'interval_minutes': value}), 'quiet_news.interval_minutes')

    def test_urgent_every_must_be_a_whole_number_from_zero_to_one_hundred(self):
        for value in (-1, 101, True, 2.5, '3', None):
            with self.subTest(value=value):
                self.assertError(self.news({'interval_minutes': 60, 'urgent_every': value}), 'quiet_news.urgent_every')

    def test_the_instruction_must_be_text_and_a_long_one_only_warns(self):
        self.assertError(self.news({'interval_minutes': 60, 'instruction': None}), 'quiet_news.instruction must be text')
        self.assertError(self.news({'interval_minutes': 60, 'instruction': 5}), 'quiet_news.instruction must be text')
        report = self.news({'interval_minutes': 60, 'instruction': 'a' * 401})
        self.assertEqual(report.errors, [])
        self.assertWarning(report, 'quiet_news.instruction is longer than 400 characters')
        self.assertNoNewsComplaint(self.news({'interval_minutes': 60, 'instruction': 'a' * 400}))

    def test_an_unknown_key_is_a_warning_not_an_error(self):
        report = self.news({'interval_minutes': 60, 'mood': 1})
        self.assertEqual(report.errors, [])
        self.assertWarning(report, "unknown key 'mood'")


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
        home = isolated_home(self)
        write(self.dir, 'theme.json', {'preset': 'neon'})
        (self.dir / 'canon.sqlite').write_bytes(b'')
        code, output = self.run_cli('campaign', 'new', 'demo')
        self.assertIn('Fix the errors above first', str(code))
        self.assertEqual(list((home / 'campaigns').iterdir()), [])

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

    def test_a_cost_table_that_is_not_an_object_is_an_error_and_never_reaches_the_campaign(self):
        for key in ('full_power_cost_percent', 'free_output_up_to_percent'):
            for bad in ('x', ['gale'], 5, None):
                with self.subTest(key=key, bad=bad):
                    sheet = json.loads(json.dumps(self.SHEET))
                    sheet['rules']['costs'] = {'fixed_cost_points': {'gale': 5}, key: bad}
                    report = self.report(character_json=sheet)
                    self.assertError(report, f'rules.costs.{key} must be an object')
                    tables = pack.campaign_config(self.dir, {})['spell_cost_rules']
                    self.assertEqual(tables[key], {})
        sheet = json.loads(json.dumps(self.SHEET))
        sheet['rules']['costs'] = {'full_power_cost_percent': {'gale': 30}, 'free_output_up_to_percent': {'gale': 10}}
        self.assertEqual(self.report(character_json=sheet).errors, [])


class ThemeOpeningTests(unittest.TestCase):
    def test_opening_minute_of_day_is_kept_only_when_valid(self):
        from novel_engine.pack import clean_theme
        self.assertEqual(clean_theme({'opening_minute_of_day': 1300})['opening_minute_of_day'], 1300)
        for bad in (1440, -1, '8:00', True, 12.5):
            self.assertNotIn('opening_minute_of_day', clean_theme({'opening_minute_of_day': bad}))
        self.assertNotIn('opening_minute_of_day', clean_theme({}))


if __name__ == '__main__':
    unittest.main()
