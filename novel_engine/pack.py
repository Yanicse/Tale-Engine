"""Novel pack: the files that make one novel playable, and their validation.

novels/<slug>/
  novel.json      title, language, tracked_npcs, currency_rates, world_rules
  character.json  the player's protagonist (name, hp_max, description, rules)
  theme.json      reader presentation: preset, colors, labels, icons, texts
  canon.sqlite    generated, ignored by git

`campaign_config` turns a pack into the campaign configuration; nothing else in the engine
knows a novel by name.
"""
import json
import re
from pathlib import Path

from .ui_text import KEYS

PRESETS = ('auto', 'classic', 'dark-fantasy', 'arcane', 'frost', 'scifi')
COLOR_KEYS = ('background', 'text', 'muted', 'line', 'card', 'accent', 'gold', 'blood', 'player')
COLOR_VALUE = re.compile(r'^(#[0-9a-fA-F]{3,8}|rgba?\([0-9., %]+\))$')
LABEL_KEYS = KEYS
ICON_CATEGORIES = ('objects', 'abilities', 'places')
ICON_NAME = re.compile(r'^[a-z0-9-]{1,60}$')
ICON_PATTERN = re.compile(r"^[a-z0-9 |'-]{1,120}$")
ICON_DIR = Path(__file__).resolve().parent / 'resources' / 'icons'
TEXT_LIMIT = 200
LABEL_LIMIT = 200


def read(path, required=False):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        if required:
            raise ValueError(f'{Path(path).name} is missing.')
        return {}
    except ValueError as error:
        raise ValueError(f'{Path(path).name} is not valid JSON: {error}')
    if not isinstance(data, dict):
        raise ValueError(f'{Path(path).name} must contain an object.')
    return data


def text(value, limit=TEXT_LIMIT):
    return value.strip()[:limit] if isinstance(value, str) else ''


def clean_icons(icons):
    """Valid [pattern, icon] pairs per category; anything else is dropped."""
    result = {}
    for category, pairs in (icons if isinstance(icons, dict) else {}).items():
        if category not in ICON_CATEGORIES or not isinstance(pairs, list):
            continue
        kept = [[p[0], p[1]] for p in pairs if isinstance(p, list) and len(p) == 2
                and isinstance(p[0], str) and isinstance(p[1], str)
                and ICON_PATTERN.match(p[0].casefold()) and ICON_NAME.match(p[1])
                and (ICON_DIR / (p[1] + '.svg')).is_file()]
        if kept:
            result[category] = [[p[0].casefold(), p[1]] for p in kept]
    return result


def clean_theme(theme):
    """Presentation block served to the reader, reduced to safe values."""
    theme = theme if isinstance(theme, dict) else {}
    preset = theme.get('preset') if theme.get('preset') in PRESETS else 'auto'
    colors = {k: v for k, v in (theme.get('colors') or {}).items()
              if k in COLOR_KEYS and isinstance(v, str) and COLOR_VALUE.match(v)}
    labels = {k: text(v, LABEL_LIMIT) for k, v in (theme.get('labels') or {}).items() if k in LABEL_KEYS and text(v, LABEL_LIMIT)}
    return {'preset': preset, 'colors': colors, 'labels': labels, 'icons': clean_icons(theme.get('icons')),
            'title': text(theme.get('title')), 'subtitle': text(theme.get('subtitle')),
            'input_hint': text(theme.get('input_hint')), 'clock': theme.get('clock', True) is not False}


def clean_npcs(npcs):
    result = []
    for item in npcs if isinstance(npcs, list) else []:
        ident = re.sub(r'[^a-z0-9_]+', '_', text(item.get('id') or item.get('name'), 60).casefold()).strip('_') \
            if isinstance(item, dict) else ''
        if ident and text(item.get('name'), 80):
            result.append({'id': ident, 'name': text(item['name'], 80), 'place': text(item.get('place'), 120),
                           'goal': text(item.get('goal'), 300)})
    return result


def seed_powers(sheet):
    """Initial `powers` of the campaign: the passives and abilities declared on the character sheet, so the
    abilities tab and the narrator know them from the first turn."""
    rules = sheet.get('rules') if isinstance(sheet.get('rules'), dict) else {}
    powers = {}
    for kind in ('passives', 'abilities'):
        for key, spec in (rules.get(kind) or {}).items():
            if isinstance(spec, dict) and re.fullmatch(r'[a-z0-9_]{1,40}', str(key)):
                powers[key] = {'actor_id': 'player', 'references': ['pack:character_sheet'], 'audience': ['narrator', 'player'],
                               'description': text(spec.get('description') or spec.get('label') or key, 300),
                               'limits': text(spec.get('limits'), 300) or 'As described on the character sheet.'}
    return powers


def campaign_config(pack_dir, base):
    """Campaign configuration from a pack, on top of the engine's `base` configuration."""
    pack_dir = Path(pack_dir)
    novel = read(pack_dir / 'novel.json', required=True)
    sheet = read(pack_dir / 'character.json')
    theme = clean_theme(read(pack_dir / 'theme.json'))
    config = {**base, 'fictional': False, 'resource_owners': {}, 'seed_references': [], 'rule_ids': [],
              'player_name': text(sheet.get('name'), 80) or 'Hero',
              'player_description': text(sheet.get('description'), 2000),
              'narration_language': text(novel.get('language'), 20) or 'en',
              'reader': {**theme, 'title': theme['title'] or text(novel.get('title'))},
              'tracked_npcs': clean_npcs(novel.get('tracked_npcs'))}
    rules = sheet.get('rules')
    if isinstance(rules, dict) and rules:
        # `costs` becomes the cost table of the abilities; the rest are the character rules.
        costs = rules.get('costs')
        config['character_rules'] = {'hp_max': sheet.get('hp_max', 100), **{k: v for k, v in rules.items() if k != 'costs'}}
        if isinstance(costs, dict) and isinstance(costs.get('full_power_cost_percent'), dict):
            config['spell_cost_rules'] = {'default_output_percent': costs.get('default_output_percent', 10),
                                          'full_power_cost_percent': costs['full_power_cost_percent'],
                                          'free_output_up_to_percent': costs.get('free_output_up_to_percent', {})}
    rates = novel.get('currency_rates')
    if isinstance(rates, dict) and rates:
        # `currencies` is the tracked purse (none for a pack); the rates are for the narrator.
        config['possession_rules'] = {'currencies': {}, 'currency_rates': rates}
    world = [text(r, 400) for r in novel.get('world_rules') or [] if text(r, 400)]
    if world:
        config['world_rules'] = world[:30]
    return config
