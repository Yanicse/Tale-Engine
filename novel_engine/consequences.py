"""Targeted public assertions; no exhaustive interpretation of the prose."""
import json
from copy import deepcopy

from .campaign import pointer
from .character import charge_resources

FINAL_STATE = '@final_state'
CALCULATED_KINDS = {'arrival', 'acquisition', 'payment', 'damage', 'healing', 'charge', 'mana'}


def materialize_public(after, events):
    """Complete only the requested values, after all the turn's computations."""
    resolved = deepcopy(events)
    for event in resolved:
        for claim in event.get('public_consequences', []):
            if claim['value_json'] != FINAL_STATE:
                continue
            if claim['kind'] not in CALCULATED_KINDS:
                raise ValueError('Automatic value reserved for computable consequences.')
            group, key = pointer(after, claim['path'])
            if key not in group:
                raise ValueError('Consequence computed on a missing value.')
            claim['value_json'] = json.dumps(group[key], ensure_ascii=False, allow_nan=False)
    return resolved


COMMITMENT_FIELDS = ('reminder_location', 'description', 'status', 'references', 'audience')
COMMITMENT_STATUS = {'open': 'open', 'kept': 'kept', 'broken': 'broken',
                     'accepted': 'open', 'active': 'open', 'pending': 'open', 'in_progress': 'open',
                     'ongoing': 'open', 'offered': 'open', 'completed': 'kept', 'complete': 'kept',
                     'done': 'kept', 'fulfilled': 'kept', 'failed': 'broken', 'abandoned': 'broken'}


def normalize_commitments(event):
    """A commitment written as a free-form object is brought to the state schema
    (description, status open/kept/broken, references, audience; nothing else). The description
    falls back on title, summary, name or the key; the status on a known synonym or 'open'; the
    references on the event's first cause; the audience on the event's. Extra fields are dropped.
    Only a set on /commitments/<key> with an object value is touched."""
    changes = event.get('changes') or []
    if not any(c.get('operation') == 'set' and str(c.get('path', '')).startswith('/commitments/')
               and isinstance(c.get('value'), dict) for c in changes):
        return event
    fixed = deepcopy(event)
    for change in fixed['changes']:
        path = str(change.get('path', ''))
        value = change.get('value')
        if change.get('operation') != 'set' or not path.startswith('/commitments/') or not isinstance(value, dict):
            continue
        key = path.split('/')[-1]
        description = next((value[name] for name in ('description', 'title', 'summary', 'name')
                            if isinstance(value.get(name), str) and value[name].strip()), key.replace('_', ' '))
        status = value.get('status')
        references = [r for r in value.get('references', []) if isinstance(r, str) and r] \
            if isinstance(value.get('references'), list) else []
        audience = [a for a in value.get('audience', []) if isinstance(a, str) and a] \
            if isinstance(value.get('audience'), list) else []
        clean = {'description': description,
                 'status': COMMITMENT_STATUS.get(str(status).lower(), 'open'),
                 'references': references or list(event.get('causes') or [])[:1] or ['action:unknown'],
                 'audience': audience or list(event.get('audience') or ['narrator', 'player'])}
        if isinstance(value.get('reminder_location'), str) and value['reminder_location']:
            clean['reminder_location'] = value['reminder_location'][:120]
        change['value'] = clean
    return fixed


RECONCILED_KINDS = {'commitment', 'knowledge', 'project'}


def reconcile_public(after, events):
    """The final state is authoritative for declarative consequences. A commitment, a piece
    of knowledge or a project that the narrator announced but did not write is dropped from the public
    list; one whose value differs from the final state takes that value. Computable consequences
    (damage, payment, acquisition...) are never touched."""
    resolved = deepcopy(events)
    for event in resolved:
        if 'public_consequences' not in event:
            continue
        kept = []
        for claim in event['public_consequences']:
            if claim['kind'] in RECONCILED_KINDS and claim['value_json'] != FINAL_STATE:
                try:
                    group, key = pointer(after, claim['path'])
                except (ValueError, KeyError, TypeError):
                    kept.append(claim)
                    continue
                if key not in group:
                    continue
                claim['value_json'] = json.dumps(group[key], ensure_ascii=False, allow_nan=False)
            kept.append(claim)
        event['public_consequences'] = kept
    return resolved


def same_value(left,right):
    return json.dumps(left,sort_keys=True,allow_nan=False)==json.dumps(right,sort_keys=True,allow_nan=False)


def validate_public(before,after,events,config,source_audience):
    for event in events:
        for claim in event.get('public_consequences',[]):
            if 'player' not in event['audience']:
                raise ValueError('Public consequence on a private event.')
            kind=claim['kind'];path=claim['path'];expected=json.loads(claim['value_json'])
            group,key=pointer(after,path)
            if key not in group or not same_value(group[key],expected):
                raise ValueError('Public consequence differs from the final state: '+path)
            if isinstance(expected,dict) and 'audience' in expected and 'player' not in expected['audience']:
                raise ValueError('Private object in a public consequence.')
            source=claim['source_reference']
            if source!=event['event_id'] and source not in event['causes']:
                raise ValueError('Public origin with no link to the event.')
            category=path.split('/')[1]
            if category in ('knowledge','intentions') and key!='player':
                raise ValueError('Private knowledge or intention of another actor in the public view.')
            if category=='resources':
                owner=config.get('resource_owners',{}).get(key)
                if owner!='player' and not key.startswith('player_'):
                    raise ValueError('Resource not attributed to the player in the public view.')
            if kind=='knowledge':
                audience=source_audience(source,events)
                if audience is None or 'player' not in audience:
                    raise ValueError('Knowledge acquired without a public observation or revelation.')
                if path!='/knowledge/player':
                    raise ValueError('Public knowledge outside the player.')
            if kind=='arrival' and path!='/positions/player':
                raise ValueError('Arrival without a player position.')
            if kind in ('acquisition','payment'):
                required=('purchase','sale','acquire','craft','transfer','gift') if kind=='acquisition' else ('payment','purchase','sale','exchange')
                if not any(op['kind'] in required for op in event.get('asset_operations',[])):
                    raise ValueError('Purchase or payment declared without a material operation.')
                if kind=='acquisition' and not path.startswith('/inventory/'):
                    raise ValueError('Acquisition outside the inventory.')
                if kind=='acquisition':
                    related = [op for op in event.get('asset_operations', [])
                               if any(item['item_id']==key for item in op['outputs'])
                               or (op['kind'] in ('transfer','gift') and any(item['item_id']==key for item in op['inputs']))]
                    if not related or expected.get('owner_id')!='player':
                        raise ValueError('Acquisition without an operation on this player item.')
                if kind=='payment' and path not in {'/resources/'+k for k in config.get('possession_rules',{}).get('currencies',{})}:
                    raise ValueError('Payment outside the installed money counter.')
                if kind=='payment' and not any((op['money_resource']==key and op['money_delta'])
                                               or (op['kind']=='exchange' and op['destination']==key)
                                               for op in event.get('asset_operations', [])):
                    raise ValueError('Payment without an operation on this currency.')
            if kind in ('damage','healing','charge','mana'):
                if not config.get('mechanics'):
                    raise ValueError('Public mechanical consequence without an installed mode.')
                old=before['resources'].get(key)
                if not path.startswith('/resources/') or type(old) is not int or type(expected) is not int:
                    raise ValueError('Mechanical consequence outside a resource.')
                if kind=='damage' and (key!='player_hp' or expected>=old):raise ValueError('Damage without a final HP decrease.')
                if kind=='healing' and (key!='player_hp' or expected<=old):raise ValueError('Healing without a final HP increase.')
                if kind=='charge' and (key not in charge_resources(config.get('character_rules')) or old==expected):
                    raise ValueError('Charge without a computed change.')
                if kind=='mana' and (key != 'player_mana' or old == expected
                                    or not any(op['kind'] in ('ability', 'restore_mana')
                                               for e in events for op in e.get('mechanical_operations', []))):
                    raise ValueError('Mana consequence without a computed change.')
            if kind=='project' and not path.startswith('/projects/'):raise ValueError('Project outside /projects/.')
            if kind=='commitment' and not path.startswith('/commitments/'):raise ValueError('Commitment outside /commitments/.')
            if kind in ('recall','intention'):
                prior,_key=pointer(before,path)
                if not same_value(prior.get(_key),expected):
                    raise ValueError('Reminder or intention turned into a new accomplishment.')


def public_projection(events):
    """Verified view of public values, without private causes or references."""
    def visible(value):
        if isinstance(value,list):return [visible(item) for item in value]
        if isinstance(value,dict):return {key:visible(item) for key,item in value.items() if key not in ('references','causes','source_reference','audience')}
        return value
    return [{'kind':c['kind'],'path':c['path'],'value':visible(json.loads(c['value_json']))}
            for e in events if 'player' in e['audience'] for c in e.get('public_consequences',[])]
