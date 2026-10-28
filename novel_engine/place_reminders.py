"""Sourced reminders at the current place; presenting them resolves no matter."""
from copy import deepcopy
import hashlib

from .contracts import check_json
from .prompts import load as load_prompt


def project(state, agenda, session, recipient='narrator', limit=8, budget=8000):
    location = state['positions'].get('player')
    previous = session.get('place_reminders', {})
    seen = previous.get('seen', {}) if previous.get('place') == location else {}
    candidates, decisions = [], []
    entries = {e['entry_id']:e for e in agenda.get('events', [])}
    linked = set()
    for key, commitment in state.get('commitments', {}).items():
        if commitment['status'] != 'open' or recipient not in commitment['audience']: continue
        entry = entries.get('accord:'+key)
        target = commitment.get('reminder_location')
        # Automatic follow-up is a place source only if its audience allows it.
        if not target and entry and recipient in entry['audience']:
            target = entry['location']
        if not target or target != location: continue
        item = {'id':'accord:'+key, 'type':'commitment', 'place':target,
                'text':commitment['description'], 'references':list(commitment['references']),
                'audience':list(commitment['audience'])}
        if entry and entry['location']==target and recipient in entry['audience']:
            linked.add(entry['entry_id'])
            item['agenda_followup'] = entry['entry_id']
            item['references'].append(entry['reference'])
            item['planned_date'] = entry['due_minute']
            item['agenda_revision'] = entry['revision']
            item['audience'] = sorted(set(item['audience']) & set(entry['audience']))
        candidates.append(item)
    for entry in entries.values():
        if entry['entry_id'] in linked or entry['status'] not in ('planned','postponed'): continue
        if recipient not in entry['audience'] or entry['location'] != location: continue
        # An old automatic follow-up does not reactivate an already closed commitment.
        if entry['entry_id'].startswith('accord:') and entry['entry_id'][7:] in state.get('commitments', {}):
            if state['commitments'][entry['entry_id'][7:]]['status'] != 'open': continue
        from .campaign import conditions_hold
        if not conditions_hold(state, entry.get('conditions', [])): continue
        candidates.append({'id':'agenda:'+entry['entry_id'], 'type':'agenda', 'place':location,
            'text':entry['title'], 'references':[entry['reference'], *entry['references']],
            'audience':list(entry['audience']), 'planned_date':entry['due_minute'],
            'agenda_revision':entry['revision'], 'agenda_followup':entry['entry_id']})
    for key, value in state.get('projects', {}).items():
        if value['status'] not in ('in_progress','suspended'): continue
        if recipient not in value['audience'] or value.get('location') != location: continue
        candidates.append({'id':'project:'+key, 'type':'project', 'place':location,
            'text':value['title'], 'status':value['status'],
            'references':list(value['references']), 'audience':list(value['audience'])})
    output, used = [], 0
    for item in sorted(candidates, key=lambda v:v['id']):
        item['fingerprint'] = hashlib.sha256(check_json(item).encode('utf-8')).hexdigest()
        size = len(check_json(item).encode('utf-8'))
        reason = ('already_present_during_visit' if seen.get(item['id'])==item['fingerprint'] else
                  'reminder_limit' if len(output)>=limit else 'reminder_budget' if used+size>budget else None)
        decisions.append({'id':item['id'], 'reason':reason or 'current_place',
                          'status':'discarded' if reason else 'kept'})
        if reason is None: output.append(item); used += size
    return output, {'place':location, 'reminders':decisions, 'utf8_bytes':used, 'byte_budget':budget}


def record(session, before, after, events, reminders):
    """Presentation receipt, only inside the transaction of a recorded turn."""
    location = before['positions'].get('player')
    destination = after['positions'].get('player')
    moved = destination != location or any(
        c['path']=='/positions/player' and (c['operation']=='remove' or c['value']!=location)
        for event in events for c in event['changes'])
    previous = session.get('place_reminders', {})
    if moved:
        session['place_reminders'] = {'place':destination, 'seen':{}}
    elif reminders:
        seen = deepcopy(previous.get('seen', {})) if previous.get('place')==location else {}
        seen.update({r['id']:r['fingerprint'] for r in reminders if r['place']==location})
        session['place_reminders'] = {'place':location, 'seen':seen}


INSTRUCTIONS = load_prompt('place_reminders.instructions')
