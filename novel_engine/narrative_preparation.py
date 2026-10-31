"""Extractive sheet and instructions selected locally; exact proofs kept."""
from copy import deepcopy
import re
import hashlib

from . import instructions as text
from .narrative_coherence import INSTRUCTIONS as COHERENCE
from .possessions import _plain
from .contracts import check_json


ALL_MODULES = ('agenda', 'canon', 'abilities', 'combat', 'memory', 'world', 'possessions', 'time', 'vitality')


def select_modules(packet):
    state = packet['current_state']
    action = _plain(packet.get('player_input', ''))
    history = packet.get('recent_exchanges', [])
    scene = next((e['content'] for e in reversed(history) if e['role']=='narrator'),
                 packet.get('last_recorded_text', ''))
    context = action + ' ' + _plain(scene)
    context += ' ' + _plain(' '.join(e['content'] for e in history[-6:]))
    # Private initiatives count even if the player does not mention them.
    context += ' ' + _plain(check_json(packet.get('world_agenda', {}).get('events', [])))
    context += ' ' + _plain(check_json(packet.get('world_planning', {})))
    modules = {'memory'}
    patterns = {
        'possessions': r'\b(?:buy\w*|bought|sell\w*|sold|sale|pay\w*|paid|purse|money|coins?|trade|market\w*|craftsm\w*|craft\w*|site|project\w*|work|materials?|inventory|recover\w*|bring\w*|brought|consum\w*|equip\w*|objects?|items?|offer\w*|gift|giv\w*|gave)\b',
        'abilities': r'\b(?:spells?|powers?|abilit\w*|sorcer\w*|magic\w*|mesmer\w*|blood|bit\w*|bite|dominat\w*|teleport\w*|mist|transform\w*|cast\w*|summon\w*|activat\w*)\b',
        'combat': r'\b(?:combat\w*|attack\w*|strik\w*|struck|threat\w*|aggress\w*|enem\w*|wound\w*|dead|death|danger\w*|ambush\w*|defend\w*|defen\w*|kill\w*|weapons?|sword|claws?)\b',
        'time': r'\b(?:wait\w*|until|trip|travel\w*|journey|road|town|city|night|evening|dinner|tomorrow|hours?|minutes?|seconds?)\b',
    }
    for name, pattern in patterns.items():
        if re.search(pattern, action if name=='time' else context):
            modules.add(name)
    resources = state.get('resources', {})
    if resources.get('player_combat_active'):
        modules.add('combat')
    if resources.get('player_hp', 0) < resources.get('player_hp_max', 0):
        modules.add('vitality')
    for key in state.get('powers', {}):
        if _plain(key).replace('_', ' ') in context:
            modules.add('abilities')
    agenda = packet.get('world_agenda', {})
    if agenda.get('events') or agenda.get('interrupted_goal') or agenda.get('max_horizon_if_limited') is not None:
        modules.add('agenda')
    if packet.get('world_planning'):
        modules.add('world')
    if packet.get('documentary_canon'):
        modules.add('canon')
    # An action that is hard to classify keeps all the available instructions.
    clear = re.search(('\\b(?:hello|hi|thanks|thank|talk\\w*|speak\\w*|discuss\\w*|ask\\w*|answer\\w*|reply\\w*|consult\\w*|read\\w*|look\\w*|observ\\w*|listen\\w*|who|what|which|how|why)\\b'), action)
    if not clear and not any(re.search(pattern, action) for pattern in patterns.values()):
        modules.update(patterns)
    if 'combat' in modules:
        modules.update(('abilities', 'vitality'))
    if 'time' in modules:
        modules.add('agenda')
    return sorted(modules)


def build_instructions(packet, modules, word_target):
    modules = set(modules)
    language = packet.get('campaign_rules', {}).get('narration_language', 'English')
    blocks = [text.OPENING, text.STORY.format(word_target=word_target), text.narration_language_block(language), text.TRANSPORT,
              text.MEMORY, text.STRUCTURE, text.DIALOGUE]
    from .creation_policy import INSTRUCTIONS
    blocks.append(INSTRUCTIONS)
    # Base layer even when a simple dialogue produces an agreement or a gift.
    blocks.append((('Durable facts require the matching events. Any acquisition, expense, consumption or '
                    'transfer uses asset_operations; unknown quantities stay null. Any newly accepted '
                    'promise or effective world initiative has a dated next step with a cause; do not treat '
                    'it as accomplished. The deadlines in the packet must be examined even if the action '
                    'does not mention them. ')))
    blocks.append((('Do not arbitrarily assign levels, distances or chances that are absent from the '
                    'sources. A voluntary HP expense does not benefit from the prevention of a lethal '
                    'attack. ')))
    if 'canon' in modules:
        blocks.append(text.CANON)
    else:
        blocks.append('The canon is documentary and private; never import the novel as a played past. ')
    if 'world' in modules:
        blocks.append(text.WORLD)
    if 'possessions' in modules:
        blocks.append(text.POSSESSIONS)
    rules = packet.get('campaign_rules', {})
    if modules & {'abilities', 'vitality', 'time', 'combat'}:
        if rules.get('mechanics'):
            mechanical = text.MECHANICS.replace(
                ('public_consequences declares the important observable consequences, with kind, path and '
                 'value_json matching the FINAL state of the turn.'),
                ('public_consequences declares kind, path, source_reference and value_json=@final_state '
                 'for arrival, acquisition, payment, damage, healing, mana and charge. The program fills in the '
                 'final value after all computations. Do not anticipate these values. For knowledge, '
                 'commitment, project, recall and intention, value_json stays a declared JSON value.'))
            blocks.append(mechanical)
        elif rules.get('spell_cost_rules') or rules.get('character_rules'):
            blocks.append(text.OLD_MECHANICS)
    if 'agenda' in modules:
        blocks.extend((text.AGENDA, text.CHRONICLE_REPORTS))
    from .agenda_protocol import INSTRUCTIONS
    blocks.extend((INSTRUCTIONS, COHERENCE))
    result = ''.join(blocks)
    if rules.get('mechanics'):
        result = result.replace('envelope is version 1.2', 'envelope is version 1.3')
    return result


def situation_sheet(packet):
    """Short factual navigation, without rewriting the dialogues or inventing knowledge."""
    state = packet['current_state']
    location = state['positions'].get('player')
    action = _plain(packet.get('player_input', ''))
    actors = [key for key, value in state['positions'].items() if key!='player' and value==location]
    projects = [key for key, value in state.get('projects', {}).items()
                if value.get('location')==location or _plain(key).replace('_', ' ') in action]
    agenda = packet.get('world_agenda', {})
    return {'place': location, 'actors_at_same_place': actors,
            'scene': 'recent_exchanges' if packet.get('recent_exchanges') else 'last_recorded_text',
            'continuity': ['extra_facts', 'recalled_exchanges', 'files'],
            'concerned_projects': projects,
            'means': {'equipped_objects': [key for key, value in state.get('inventory', {}).items() if value.get('equipped')],
                       'abilities': 'character_abilities' if 'character_abilities' in packet else 'current_state.powers'},
            'constraints': {'max_horizon_if_limited': agenda.get('max_horizon_if_limited'),
                           'interrupting_deadlines': [e['entry_id'] for e in agenda.get('events', []) if e.get('interrupt')],
                           'interrupted_goal': agenda.get('interrupted_goal')},
            'private_information': ['world_agenda', 'world_planning', 'documentary_canon'],
            'instruction': ('Positions attest a location, not a perception or knowledge. The indicated blocks '
                         'contain the exact texts and proofs; unknowns stay unknown.')}


def select_documents(documents, modules, packet):
    """Remove only the known off-topic paragraphs; keep any unclassified addition."""
    modules = set(modules)
    result = []
    mechanics = packet.get('campaign_rules', {}).get('mechanics')
    for document in documents:
        source = document['source']
        selected = []
        heading = ''
        for paragraph in re.split(r'\n\s*\n', document['text']):
            match = re.match(r'^## (.+)', paragraph)
            if match:
                heading = match.group(1).strip()
            skip = False
            signature = hashlib.sha256(paragraph.encode('utf-8')).hexdigest()
            duplicate = signature in text.DOCUMENT_DUPLICATES.get(source, [])
            known = signature in text.KNOWN_DOCUMENT_PARAGRAPHS.get(source, [])
            if source == 'docs/engine/POSSESSIONS.md':
                if 'possessions' not in modules:
                    skip = heading in ('Inventory', 'Purse and operations', 'Projects')
            elif source == packet.get('campaign_rules', {}).get('character_sheet'):
                skip = heading == 'Technical scope'
                if not modules & {'abilities', 'combat', 'vitality', 'time'}:
                    skip = heading in ('Common costs and dosage', 'Technical scope')
                elif mechanics and heading == 'Common costs and dosage':
                    # Installed rules and computed effects are authoritative; keep the qualitative limits.
                    skip = paragraph.startswith('|') or paragraph.startswith('All paid costs')
            elif source == 'docs/engine/agents/narrator.md':
                skip = paragraph.startswith(('For a narrator answering by hand or through a chat assistant,', 'Do not write directly to the campaign.'))
                if paragraph.startswith(('In envelope 1.2,', 'In envelope 1.2,')):
                    skip = 'agenda' not in modules
                if paragraph.startswith('Possessions, currencies and projects'):
                    skip = 'possessions' not in modules
                if paragraph.startswith('The documentary_canon selection'):
                    skip = 'canon' not in modules
                if paragraph.startswith('Progressive summaries') or paragraph.startswith('The world also takes'):
                    skip = 'world' not in modules
            # The other documents, including the private preparation, stay exact and whole.
            if not known or (not skip and not duplicate) or match:
                selected.append(paragraph)
        item = deepcopy(document)
        item['text'] = '\n\n'.join(selected)
        result.append(item)
    return result
