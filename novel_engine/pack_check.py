"""`novel validate`: every problem of a novel pack, with the file and the key, instead of silent clean-up.

pack.clean_* keep the campaign safe by dropping what is invalid; this module says what was wrong so the
author can fix it. Errors stop `novel campaign new`; warnings do not.
"""
import json
import re
from pathlib import Path

from .pack import COLOR_KEYS, COLOR_VALUE, ICON_CATEGORIES, ICON_DIR, ICON_NAME, ICON_PATTERN, PRESETS, TEXT_LIMIT
from .ui_text import EN, PACKS, PLACEHOLDER, language_code

NOVEL_KEYS = {'title', 'language', 'tracked_npcs', 'currencies', 'currency_rates', 'world_rules', 'opening'}
THEME_KEYS = {'preset', 'colors', 'labels', 'icons', 'title', 'subtitle', 'input_hint', 'clock'}
SHEET_KEYS = {'name', 'hp_max', 'description', 'rules'}
RULE_KEYS = {'passives', 'abilities', 'forbidden_resources', 'costs'}
NEEDS_VITALITY = {'charges': 'charges that refill per night or per combat', 'regeneration': 'regeneration by form',
                  'solar_schedule': 'a day and night schedule', 'version': 'the vitality mechanics version'}
ABILITY_KEYS = {'label', 'phase', 'free', 'description', 'limits'}
COST_KEYS = {'default_output_percent', 'free_output_up_to_percent', 'full_power_cost_percent'}
IDENT = re.compile(r'^[a-z0-9_]{1,40}$')
WORLD_RULES_MAX = 30


class Report:
    def __init__(self):
        self.errors, self.warnings = [], []

    def error(self, where, message):
        self.errors.append(f'{where}: {message}')

    def warn(self, where, message):
        self.warnings.append(f'{where}: {message}')

    @property
    def ok(self):
        return not self.errors


def load(path, report, required):
    name = Path(path).name
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        if required:
            report.error(name, 'file is missing')
        return None
    except ValueError as error:
        report.error(name, f'not valid JSON ({error})')
        return None
    if not isinstance(data, dict):
        report.error(name, 'must contain a JSON object')
        return None
    return data


def unknown_keys(data, allowed, name, report):
    for key in sorted(set(data) - allowed):
        report.warn(name, f"unknown key '{key}' is ignored (known: {', '.join(sorted(allowed))})")


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_novel(novel, report):
    name = 'novel.json'
    unknown_keys(novel, NOVEL_KEYS, name, report)
    if not isinstance(novel.get('title'), str) or not novel['title'].strip():
        report.warn(name, "no 'title': the pack folder name is used")
    language = novel.get('language', 'en')
    if not isinstance(language, str) or not language.strip():
        report.error(name, "'language' must be a text such as 'en' or 'fr'")
    elif language_code(language) == 'en' and not language.strip().casefold().startswith(('en', 'anglais')):
        report.warn(name, f"language '{language}' has no interface pack ({', '.join(PACKS)}): the page stays in English; "
                          "the narration still follows it, and 'labels' in theme.json can translate the page")
    npcs = novel.get('tracked_npcs', [])
    if not isinstance(npcs, list):
        report.error(name, "'tracked_npcs' must be a list")
    else:
        seen = set()
        for number, item in enumerate(npcs):
            where = f'{name}: tracked_npcs[{number}]'
            if not isinstance(item, dict) or not isinstance(item.get('name'), str) or not item['name'].strip():
                report.error(name, f'tracked_npcs[{number}] needs a text "name"')
                continue
            ident = re.sub(r'[^a-z0-9_]+', '_', str(item.get('id') or item['name']).casefold()).strip('_')
            if ident in seen:
                report.error(name, f"tracked_npcs[{number}]: duplicate id '{ident}'")
            seen.add(ident)
            for key, limit in (('place', 120), ('goal', 300), ('name', 80)):
                if isinstance(item.get(key), str) and len(item[key]) > limit:
                    report.warn(name, f'tracked_npcs[{number}].{key} is longer than {limit} characters and is cut')
            if not isinstance(item.get('goal'), str) or not item.get('goal', '').strip():
                report.warn(name, f"tracked_npcs[{number}] ('{item['name']}') has no goal: it will not act off screen")
    currencies = novel.get('currencies', {})
    if not isinstance(currencies, dict):
        report.error(name, "'currencies' must be an object of currency id -> {label, initial_balance}")
    else:
        for key, spec in currencies.items():
            if not IDENT.fullmatch(key) or key in {'hp', 'hp_max', 'mana', 'mana_max', 'combat_id', 'combat_active'}:
                report.error(name, f'currencies.{key}: invalid or reserved currency id')
            if not isinstance(spec, dict):
                report.error(name, f'currencies.{key} must be an object')
                continue
            if type(spec.get('initial_balance', 0)) is not int or spec.get('initial_balance', 0) < 0:
                report.error(name, f'currencies.{key}.initial_balance must be a non-negative integer')
            if 'label' in spec and (not isinstance(spec['label'], str) or not spec['label'].strip()):
                report.error(name, f'currencies.{key}.label must be a non-empty text')
    rates = novel.get('currency_rates', {})
    if not isinstance(rates, dict):
        report.error(name, "'currency_rates' must be an object such as {\"gold\": {\"silver\": 20}}")
    else:
        for big, table in rates.items():
            if not IDENT.fullmatch(big) or big in {'hp', 'hp_max', 'mana', 'mana_max', 'combat_id', 'combat_active'}:
                report.error(name, f'currency_rates.{big}: invalid or reserved currency id')
            if not isinstance(table, dict) or not table or not all(is_number(v) and v > 0 for v in table.values()):
                report.error(name, f"currency_rates.{big} must map smaller currencies to positive numbers")
            elif any(not IDENT.fullmatch(key) or key in {'hp', 'hp_max', 'mana', 'mana_max', 'combat_id', 'combat_active'} for key in table):
                report.error(name, f'currency_rates.{big}: invalid or reserved currency id')
    rules = novel.get('world_rules', [])
    if not isinstance(rules, list) or not all(isinstance(r, str) for r in rules):
        report.error(name, "'world_rules' must be a list of sentences")
    else:
        if len(rules) > WORLD_RULES_MAX:
            report.warn(name, f'only the first {WORLD_RULES_MAX} world_rules are kept')
        for number, rule in enumerate(rules):
            if len(rule) > 400:
                report.warn(name, f'world_rules[{number}] is longer than 400 characters and is cut')
    opening = novel.get('opening')
    if opening is not None:
        if not isinstance(opening, dict) or not all(isinstance(opening.get(k), str) and opening[k].strip()
                                                    for k in ('place', 'scene')):
            report.error(name, "'opening' needs a text 'place' and a text 'scene'")
        else:
            unknown_keys(opening, {'place', 'scene'}, name + ': opening', report)


def check_theme(theme, report):
    name = 'theme.json'
    unknown_keys(theme, THEME_KEYS, name, report)
    if theme.get('preset', 'auto') not in PRESETS:
        report.error(name, f"preset '{theme.get('preset')}' is unknown (use one of: {', '.join(PRESETS)})")
    colors = theme.get('colors', {})
    if not isinstance(colors, dict):
        report.error(name, "'colors' must be an object")
    else:
        for key, value in colors.items():
            if key not in COLOR_KEYS:
                report.error(name, f"colors.{key} is not a known color (use: {', '.join(COLOR_KEYS)})")
            elif not isinstance(value, str) or not COLOR_VALUE.match(value):
                report.error(name, f"colors.{key} = {value!r} must be #hex or rgb()/rgba()")
    labels = theme.get('labels', {})
    if not isinstance(labels, dict):
        report.error(name, "'labels' must be an object")
    else:
        for key, value in labels.items():
            if key not in EN:
                report.error(name, f"labels.{key} is not a text of the page (see novel_engine/ui_text.py)")
            elif not isinstance(value, str) or not value.strip():
                report.error(name, f'labels.{key} must be a non-empty text')
            else:
                if set(PLACEHOLDER.findall(value)) != set(PLACEHOLDER.findall(EN[key])):
                    report.warn(name, f"labels.{key} should keep the placeholders {sorted(set(PLACEHOLDER.findall(EN[key])))}")
                if len(value) > 200:
                    report.warn(name, f'labels.{key} is longer than 200 characters and is cut')
    for key in ('title', 'subtitle', 'input_hint'):
        if key in theme and not isinstance(theme[key], str):
            report.error(name, f"'{key}' must be a text")
        elif isinstance(theme.get(key), str) and len(theme[key]) > TEXT_LIMIT:
            report.warn(name, f'{key} is longer than {TEXT_LIMIT} characters and is cut')
    if 'clock' in theme and not isinstance(theme['clock'], bool):
        report.error(name, "'clock' must be true or false")
    icons = theme.get('icons', {})
    if not isinstance(icons, dict):
        report.error(name, "'icons' must be an object")
        return
    for category, pairs in icons.items():
        if category not in ICON_CATEGORIES:
            report.error(name, f"icons.{category} is not a category (use: {', '.join(ICON_CATEGORIES)})")
            continue
        if not isinstance(pairs, list):
            report.error(name, f'icons.{category} must be a list of [words, icon] pairs')
            continue
        for number, pair in enumerate(pairs):
            where = f'icons.{category}[{number}]'
            if not (isinstance(pair, list) and len(pair) == 2 and all(isinstance(p, str) for p in pair)):
                report.error(name, f'{where} must be ["word|other word", "icon-name"]')
            elif not ICON_PATTERN.match(pair[0].casefold()):
                report.error(name, f"{where}: words '{pair[0]}' may only use letters, digits, spaces, |, ' and -")
            elif not ICON_NAME.match(pair[1]) or not (ICON_DIR / (pair[1] + '.svg')).is_file():
                report.error(name, f"{where}: icon '{pair[1]}' does not exist in novel_engine/resources/icons")


def check_ability(kind, key, spec, report):
    name = 'character.json'
    where = f'rules.{kind}.{key}'
    if not IDENT.match(key):
        report.error(name, f"{where}: the id may only use a-z, 0-9 and _ (40 characters at most)")
    if not isinstance(spec, dict):
        report.error(name, f'{where} must be an object')
        return
    for extra in sorted(set(spec) - ABILITY_KEYS):
        why = 'cooldowns need the vitality mechanics, not available to packs yet' if extra == 'cooldown' else 'unknown key'
        report.error(name, f"{where}.{extra}: {why}")
    if 'label' in spec and (not isinstance(spec['label'], str) or not spec['label'].strip()):
        report.error(name, f'{where}.label must be a non-empty text')
    if 'phase' in spec and not isinstance(spec['phase'], str):
        report.error(name, f'{where}.phase must be a text')
    for key in ('description', 'limits'):
        if key in spec and (not isinstance(spec[key], str) or not spec[key].strip()):
            report.error(name, f'{where}.{key} must be a non-empty text')
    if 'free' in spec and not isinstance(spec['free'], bool):
        report.error(name, f'{where}.free must be true or false')


def check_costs(costs, abilities, report):
    name = 'character.json'
    if not isinstance(costs, dict):
        report.error(name, "rules.costs must be an object")
        return
    for key in sorted(set(costs) - COST_KEYS):
        report.error(name, f'rules.costs.{key}: unknown key (use: {", ".join(sorted(COST_KEYS))})')
    default = costs.get('default_output_percent', 10)
    if not is_number(default) or not 0 < default <= 100:
        report.error(name, 'rules.costs.default_output_percent must be a number above 0 and at most 100')
    full = costs.get('full_power_cost_percent')
    if not isinstance(full, dict) or not full:
        report.error(name, 'rules.costs.full_power_cost_percent is required: ability id -> percent of maximum HP at 100% power')
        full = {}
    for key, value in full.items():
        if key not in abilities:
            report.error(name, f"rules.costs.full_power_cost_percent.{key}: no such ability in rules.abilities")
        if not is_number(value) or not 0 <= value <= 100:
            report.error(name, f'rules.costs.full_power_cost_percent.{key} must be between 0 and 100')
    free = costs.get('free_output_up_to_percent', {})
    if not isinstance(free, dict):
        report.error(name, 'rules.costs.free_output_up_to_percent must be an object')
        return
    for key, value in free.items():
        if key not in full:
            report.error(name, f"rules.costs.free_output_up_to_percent.{key}: the ability has no full power cost")
        if not is_number(value) or not 0 <= value <= 100:
            report.error(name, f'rules.costs.free_output_up_to_percent.{key} must be between 0 and 100')


def check_sheet(sheet, report):
    name = 'character.json'
    unknown_keys(sheet, SHEET_KEYS, name, report)
    if not isinstance(sheet.get('name'), str) or not sheet['name'].strip():
        report.warn(name, "no 'name': the character is called Hero")
    hp = sheet.get('hp_max', 100)
    if 'description' in sheet and not isinstance(sheet['description'], str):
        report.error(name, "'description' must be a text")
    elif len(sheet.get('description', '')) > 2000:
        report.warn(name, "'description' is longer than 2000 characters and is cut")
    if type(hp) is not int or hp < 1:
        report.error(name, "'hp_max' must be a whole number of at least 1")
    rules = sheet.get('rules', {})
    if not isinstance(rules, dict):
        report.error(name, "'rules' must be an object")
        return
    for key in sorted(rules):
        if key in NEEDS_VITALITY:
            report.error(name, f"rules.{key}: {NEEDS_VITALITY[key]} need the vitality mechanics, which packs cannot enable yet")
        elif key not in RULE_KEYS:
            report.error(name, f"rules.{key}: unknown rule (use: {', '.join(sorted(RULE_KEYS))})")
    abilities = {}
    for kind in ('passives', 'abilities'):
        group = rules.get(kind, {})
        if not isinstance(group, dict):
            report.error(name, f'rules.{kind} must be an object of id -> {{label, phase, free}}')
            continue
        for key, spec in group.items():
            check_ability(kind, key, spec, report)
            if kind == 'abilities':
                abilities[key] = spec
    if 'forbidden_resources' in rules and not (isinstance(rules['forbidden_resources'], list)
                                               and all(isinstance(r, str) and r for r in rules['forbidden_resources'])):
        report.error(name, 'rules.forbidden_resources must be a list of resource names')
    if 'costs' in rules:
        check_costs(rules['costs'], abilities, report)
    elif abilities and not any(isinstance(spec, dict) and spec.get('free') for spec in abilities.values()):
        report.warn(name, 'abilities without rules.costs cost nothing: the narrator will not charge hit points for them')


def check_pack(pack_dir):
    """Report of a pack folder: all errors (blocking) and warnings."""
    pack_dir = Path(pack_dir)
    report = Report()
    if not pack_dir.is_dir():
        report.error(str(pack_dir), 'no such novel pack folder')
        return report
    novel = load(pack_dir / 'novel.json', report, required=True)
    theme = load(pack_dir / 'theme.json', report, required=False)
    sheet = load(pack_dir / 'character.json', report, required=False)
    if novel is not None:
        check_novel(novel, report)
    if theme is not None:
        check_theme(theme, report)
    if not (pack_dir / 'character.json').is_file():
        report.warn('character.json', 'missing: run `novel character new` (the character is called Hero with 100 HP)')
    if sheet is not None:
        check_sheet(sheet, report)
    if not any((pack_dir / 'text').glob('v*.jsonl')):
        report.warn('text/', 'no ingested book yet: run `novel ingest`')
    if not (pack_dir / 'canon.sqlite').is_file():
        report.warn('canon.sqlite', 'not built yet: run `novel canon build`')
    return report
