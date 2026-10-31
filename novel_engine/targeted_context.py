"""Local, reversible projection of the prompt, with no mutation of the campaign."""
from copy import deepcopy
import json
import os
from pathlib import Path
import re

from .contracts import check_json
from .possessions import _plain
from .prompts import load as load_prompt


def enabled():
    override = os.environ.get('NOVEL_TARGETED_CONTEXT')
    if override is not None:
        return override == '1'
    try:
        settings = json.loads(Path(__file__).with_name('targeted_context_settings.json').read_text(encoding='utf-8-sig'))
        return settings.get('active') is True
    except (OSError, ValueError, AttributeError):
        # A missing or unreadable configuration falls back to the earlier behavior.
        return False


def reduction(name):
    """Additional reduction, enabled separately; NOVEL_CONTEXT_REDUCTIONS=list (empty: none) in tests."""
    if not enabled():
        return False
    override = os.environ.get('NOVEL_CONTEXT_REDUCTIONS')
    if override is not None:
        return name in override.split(',')
    try:
        settings = json.loads(Path(__file__).with_name('targeted_context_settings.json').read_text(encoding='utf-8-sig'))
        return settings.get('reductions', {}).get(name) is True
    except (OSError, ValueError, AttributeError):
        return False


def serialize(value):
    # Keep the contract's Unicode/NaN checks, only shrink the JSON whitespace.
    check_json(value)
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


# Catalog of the active abilities, declared by character_rules.abilities.<id>.catalogue:
# {"aliases": [...], "offensive": true|false, "bound": true|false}. A bound ability (for example
# tied to a weapon) stays individually retrievable and keeps its sheet when an active effect cites it.
# Any ability without a catalog entry stays whole in the packet.
HP_DANGER_RATIO = 0.7


def catalogue(packet):
    abilities = packet.get('campaign_rules', {}).get('character_rules', {}).get('abilities', {})
    return {key: spec['catalogue'] for key, spec in abilities.items() if isinstance(spec.get('catalogue'), dict)}


def bound_powers(packet):
    """Always-complete sheets: bound abilities and character_rules.always_complete_sheets."""
    rules = packet.get('campaign_rules', {}).get('character_rules', {})
    return ({key for key, value in catalogue(packet).items() if value.get('bound')}
            | set(rules.get('always_complete_sheets', [])))


def analysis(packet):
    """Local projection decision: reasons to keep everything and the powers cited by the action."""
    state = packet['current_state']
    powers = state.get('powers', {})
    action = _plain(packet.get('player_input', ''))
    history = packet.get('recent_exchanges', [])
    scene = _plain(' '.join(e['content'] for e in history[-6:]))
    local = re.sub("['’]", ' ', action + ' ' + scene)
    resources = state.get('resources', {})
    agenda = packet.get('world_agenda', {})
    threat_pattern = r'\b(combat\w*|attack\w*|ambush\w*|enem\w*|threat\w*|danger\w*|wound\w*|hurt\w*|defen\w*|flee\w*|fled|dodg\w*|aggress\w*|strik\w*|struck)\b'
    # A secret initiative can make defenses useful before any manifestation.
    private_titles = ' '.join(e.get('title', '') for e in agenda.get('events', []))
    private_titles += ' ' + ' '.join(s.get('title', '') + ' ' + s.get('goal', '')
                                    for s in packet.get('world_planning', {}).get('situations', [])
                                    if s.get('status') == 'active')
    reasons = []
    if resources.get('player_combat_active'):
        reasons.append('danger:combat_active')
    ratio = HP_DANGER_RATIO if reduction('danger_hp') else 1
    if resources.get('player_hp', 0) < resources.get('player_hp_max', 0) * ratio:
        reasons.append('danger:hp_not_max' if ratio == 1 else 'danger:hp_below_threshold')
    # An appointment or a check that interrupts is not a threat: only the risk counts.
    risky = (lambda e: e.get('kind') == 'risk') if reduction('agenda_risk') else (lambda e: e.get('interrupt') or e.get('kind') == 'risk')
    if any(risky(e) for e in agenda.get('events', [])):
        reasons.append('danger:agenda')
    # A threat is read in the action and the last three exchanges, not in the whole recent history.
    threat_text = action + ' ' + _plain(' '.join(e['content'] for e in history[-3:])) if reduction('recent_threat') else local
    if re.search(threat_pattern, threat_text + ' ' + _plain(private_titles)):
        reasons.append('danger:threat_word')
    if re.search(('\\b(wait\\w*|until|while|hours?|days?|nights?|tomorrow|trip|travel\\w*|journey|road)\\b|\\d+\\s*h\\b'), action):
        reasons.append('long_action')
    selected = set()
    declared = catalogue(packet)
    for key, value in powers.items():
        aliases = declared.get(key, {}).get('aliases', [])
        names = [_plain(key).replace('_', ' '), *(re.sub("['’]", ' ', _plain(name)) for name in aliases)]
        if any(re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', local) for name in names):
            selected.add(key)
        # Find the explicit sheet requests through the existing recovery.
        if key in packet.get('memory_recovery', {}).get('query', ''):
            selected.add(key)
    generic_power = re.search(r'\b(power\w*|abilit\w*|magic\w*|spell\w*|sorcery|cast\w*|activat\w*|us(?:e|es|ed|ing)|transform\w*)\b', action)
    ordinary = re.search(r'\b(hello|hi|thanks|thank|talk\w*|speak\w*|discuss\w*|ask\w*|answer\w*|reply\w*|consult\w*|look\w*|observ\w*|buy\w*|bought|pay\w*|paid|money|coins?|furniture|decor\w*)\b', action)
    if generic_power and not selected:
        reasons.append('generic_power_word')
    if not ordinary and not selected:
        reasons.append('non_ordinary_action')
    return reasons, selected


def project(packet):
    """Keep the useful exact sheets and a catalog of the other acquired entities.

    Dangerous, long or ambiguous scenes keep all the power sheets,
    except for the optional `current_action` reduction. Effects, resources, charges, agenda,
    memories and references stay exact. Nothing is shortened in the save.
"""
    packet = deepcopy(packet)
    reasons, selected = analysis(packet)
    _project_powers(packet, reasons, selected)
    if reduction('inventory') or reduction('index'):
        _project_inventory(packet)
    if reduction('index'):
        _project_projects(packet)
    if reduction('planning'):
        _project_planning(packet)
    return packet


def _project_powers(packet, reasons, selected):
    state = packet['current_state']
    powers = state.get('powers', {})
    declared = catalogue(packet)
    bound = {key for key, value in declared.items() if value.get('bound')}
    offensive = {key for key, value in declared.items() if value.get('offensive')}
    candidates = set(declared)
    # The weapon's sheets stay individually retrievable, even in combat.
    # Guard instructions and marks that are already active stay complete through their effects.
    effects = _plain(check_json(state.get('effects', {})))
    selected = set(selected) | {key for key in bound if key in effects}
    # Index mode: the narrator asks for the sheets itself; only an active combat or low HP keep everything.
    if reduction('index'):
        reasons = [r for r in reasons if r == 'danger:combat_active' or r.startswith('danger:hp')]
    if reasons:
        # Current action without danger or generic power word: only the uncited combat spells leave.
        if reduction('current_action') and set(reasons) <= {'long_action', 'non_ordinary_action'}:
            candidates = (set(declared) & offensive) | bound
        else:
            candidates = bound
    omitted = {key: value for key, value in powers.items() if key in candidates and key not in selected}
    if not omitted:
        return
    state['powers'] = {key: value for key, value in powers.items() if key not in omitted}
    packet['power_catalogue'] = {
        key: {'references': value.get('references', []), 'audience': value.get('audience', [])}
        for key, value in omitted.items()
    }
    if reduction('index'):
        for key, value in omitted.items():
            # The requested sheet brings its references; the catalog keeps the audience and one line.
            packet['power_catalogue'][key] = {'summary': _line(value.get('description', '')),
                                                 'audience': value.get('audience', [])}
        packet['index_instruction'] = INDEX_RULE
    packet['power_projection'] = {
        'complete_sheets': list(state['powers']), 'catalogued_sheets': list(omitted),
        'instruction': ('All the powers in the catalog are acquired. The catalog is a marker, not a sheet '
                     'sufficient to resolve a use. If a sheet becomes essential, use '
                     'memory_request.query=power:<identifier> before resolving it. Common abilities, '
                     'resources, charges and effects stay exact. Never modify a partial object as if it were '
                     'its complete state. SQLite and the validations stay complete.')}
    # The selected parameters stay strictly those of the installed registry.
    abilities = packet.get('campaign_rules', {}).get('character_rules', {}).get('abilities', {})
    for key in omitted:
        abilities.pop(key, None)
        packet.get('character_abilities', {}).get('default_power_effects', {}).pop(key, None)


def _local_text(packet):
    history = packet.get('recent_exchanges', [])
    return _plain(packet.get('player_input', '') + ' ' + ' '.join(e['content'] for e in history[-6:]))


def _words(text):
    return set(re.findall(r'[a-z0-9]{4,}', text))


def _cited(key, words):
    """An object is cited if a significant word of its identifier appears in the local text."""
    for token in _plain(key).split('_'):
        if len(token) >= 4 and (token in words or token.rstrip('s') in words or token + 's' in words):
            return True
    return False


# Up to this number of objects, no reduction. A general request also keeps the whole inventory.
INVENTORY_MIN = 6
INVENTORY_GENERIC = '\\b(inventory|items?|bag|belongings|possessions?|equipment|gear|goods|everything)\\b'


def _project_inventory(packet):
    state = packet['current_state']
    items = state.get('inventory', {})
    action = _plain(packet.get('player_input', ''))
    if len(items) <= INVENTORY_MIN or re.search(INVENTORY_GENERIC, action):
        return
    words = _words(_local_text(packet))
    query = packet.get('memory_recovery', {}).get('query', '')
    kept = {key: value for key, value in items.items()
            if value.get('equipped') or key in query or _cited(key, words)}
    omitted = {key: value for key, value in items.items() if key not in kept}
    if not omitted:
        return
    state['inventory'] = kept
    packet['object_catalogue'] = {
        key: {'summary': _line(value.get('description', '')), 'location': value.get('location'), 'quantity': value.get('quantity'),
              'quantity_precision': value.get('quantity_precision'), 'unit': value.get('unit'),
              'references': value.get('references', []), 'audience': value.get('audience', [])}
        for key, value in omitted.items()
    }
    packet['inventory_projection'] = {
        'complete_sheets': list(kept), 'catalogued_sheets': list(omitted),
        'instruction': ('All the objects in the catalog are owned. The catalog is a marker, not a sheet '
                     'sufficient to use or modify an object. If a sheet becomes essential, use '
                     'memory_request.query=object:<identifier> before resolving it. The purse, resources, '
                     'projects and effects stay exact. Never modify a partial object as if it were its '
                     'complete state.')}
    if reduction('index'):
        packet['index_instruction'] = INDEX_RULE


INDEX_RULE = load_prompt('targeted_context.index_rule')


def _line(text):
    return text.split('. ')[0][:90]


def _project_projects(packet):
    """Index mode: uncited projects in one line, exact sheet through project:<id>."""
    state = packet['current_state']
    projects = state.get('projects', {})
    words = _words(_local_text(packet))
    query = packet.get('memory_recovery', {}).get('query', '')
    omitted = {key: value for key, value in projects.items() if key not in query and not _cited(key, words)
               and not _words(_plain(value.get('title', ''))) - PLANNING_GENERIC & words}
    if not omitted:
        return
    state['projects'] = {key: value for key, value in projects.items() if key not in omitted}
    packet['project_catalogue'] = {
        key: {'title': value.get('title'), 'status': value.get('status'), 'place': value.get('location'),
              'references': value.get('references', []), 'audience': value.get('audience', [])}
        for key, value in omitted.items()}
    packet['index_instruction'] = INDEX_RULE


PLANNING_GENERIC = {'with', 'their', 'for', 'near', 'house', 'around', 'under', 'toward', 'towards', 'from'}
PLANNING_SUMMARY_KEYS = ('id', 'title', 'status', 'due_minute', 'interrupt', 'revision', 'expected_revision', 'reference')


def _project_planning(packet):
    """Active situations of the planner: whole sheet only if tied to the scene or close to their date."""
    planning = packet.get('world_planning') or {}
    situations = planning.get('situations', [])
    if not situations:
        return
    words = _words(_local_text(packet) + ' ' + _plain(packet.get('last_recorded_text', '')))
    minute = packet['current_state'].get('world_minute', 0)
    result, summarized = [], []
    for item in situations:
        related = _words(_plain(' '.join(str(item.get(k) or '') for k in ('title', 'actor', 'place')))) - PLANNING_GENERIC
        near = item.get('due_minute') is not None and item['due_minute'] <= minute + 1440
        if item.get('status') == 'active' and not (near or item.get('interrupt') or related & words):
            result.append({key: item[key] for key in PLANNING_SUMMARY_KEYS if key in item})
            summarized.append(item['id'])
        else:
            result.append(item)
    if summarized:
        planning['situations'] = result
        planning['summarized_situations'] = summarized
        planning['summary_instruction'] = (('Situations summarized to their title are not tied to the scene: their complete sheet '
                                        'stays in the save and comes back as soon as their place, people or deadline come into '
                                        'play.'))


def deduplicate_documents(documents, instructions):
    """Remove only a paragraph that is already present word for word in the instructions."""
    result = deepcopy(documents)
    for doc in result:
        paragraphs = re.split(r'\n\s*\n', doc['text'])
        doc['text'] = '\n\n'.join(p for p in paragraphs if not p.strip() or
                                    p.lstrip().startswith('#') or p.strip() not in instructions)
    return result
