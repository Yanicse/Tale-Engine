"""Extractive projection of the mentioned NPCs, with no invention, no writing and no new table."""
from .contracts import check_json
from .indexed_memory import normalized
from .prompts import load as load_prompt


def mentioned(names, text):
    padded = ' ' + normalized(text) + ' '
    return any(len(name := normalized(n)) >= 3 and ' '+name+' ' in padded for n in names)


def project(state, creations, action='', scene='', recipient='narrator', limit=6, budget=6000):
    """Mentions select a sheet, without asserting the NPC's physical presence."""
    candidates = {}
    for value in creations:
        if value['kind'] != 'npc' or recipient not in value['audience']: continue
        ident = value['entity_id']
        names = [ident, ident.replace('_', ' '), value['name'], *value.get('aliases', [])]
        explicit, recent = mentioned(names, action), mentioned(names, scene)
        if not (explicit or recent): continue
        fields = {'current_goal':None, 'declared_attitude':None}
        traits = []
        for trait in value['traits']:
            label, sep, content = trait.partition(':')
            field = {'current goal':'current_goal', 'current attitude':'declared_attitude'}.get(normalized(label))
            if field and sep and content.strip(): fields[field] = content.strip()
            else: traits.append(trait)
        candidates[ident] = (2 if explicit else 1, {
            'entity_id':ident, 'name':value['name'], 'status':value['status'],
            'traits':traits, **fields, 'unknowns':list(value['unknowns']),
            'relations':[], 'references':list(value['references']), 'audience':list(value['audience']),
            'selection':'action' if explicit else 'recent_scene'})
    # An existing relation can also provide a minimal sheet, without inventing a name.
    for ident, relation in state.get('relations', {}).items():
        if recipient not in relation.get('audience', []): continue
        for actor in (relation['from_id'], relation['to_id']):
            if actor == 'player': continue
            if actor not in candidates:
                names = [actor, actor.replace('_', ' ')]
                explicit, recent = mentioned(names, action), mentioned(names, scene)
                if not (explicit or recent): continue
                candidates[actor] = (2 if explicit else 1, {
                    'entity_id':actor, 'name':None, 'status':'relation_recorded',
                    'traits':[], 'current_goal':None, 'declared_attitude':None,
                    'unknowns':[], 'relations':[], 'references':[], 'audience':list(relation['audience']),
                    'selection':'action' if explicit else 'recent_scene'})
            sheet = candidates[actor][1]
            sheet['relations'].append({'id':ident, **relation})
            sheet['references'] = sorted(set(sheet['references']) | set(relation['references']))
            sheet['audience'] = sorted(set(sheet['audience']) & set(relation['audience']))
    sheets, decisions, used = [], [], 0
    for _, sheet in sorted(candidates.values(), key=lambda v:(-v[0], v[1]['entity_id'])):
        size = len(check_json(sheet).encode('utf-8'))
        reason = 'sheet_limit' if len(sheets) >= limit else 'sheet_budget' if used+size > budget else None
        decisions.append({'entity_id':sheet['entity_id'], 'reason':reason or sheet['selection'],
                          'status':'discarded' if reason else 'kept', 'utf8_bytes':size})
        if reason is None: sheets.append(sheet); used += size
    return sheets, {'sheets':decisions, 'utf8_bytes':used, 'byte_budget':budget}


INSTRUCTIONS = load_prompt('npc_sheets.instructions')
