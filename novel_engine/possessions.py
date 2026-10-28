"""Possessions and material operations, computed locally and atomically."""
from copy import deepcopy
import json
import re
import unicodedata

from .contracts import check_json
from .validation import validate


def operation_schema():
    def obj(properties):
        return {'type': 'object', 'properties': properties, 'required': list(properties),
                'additionalProperties': False}
    return {'type': 'array', 'maxItems': 32, 'items': obj({
        'kind': {'type': 'string', 'enum': ['payment', 'purchase', 'sale', 'craft', 'consume', 'transfer', 'gift', 'acquire']},
        'money_resource': {'type': ['string', 'null']},
        'money_delta': {'type': 'integer'},
        'inputs': {'type': 'array', 'maxItems': 32, 'items': obj({
            'item_id': {'type': 'string', 'minLength': 1},
            'quantity': {'type': ['integer', 'null'], 'minimum': 1}})},
        'outputs': {'type': 'array', 'maxItems': 32, 'items': obj({
            'item_id': {'type': 'string', 'minLength': 1},
            'item_json': {'type': 'string', 'minLength': 2}})},
        'destination': {'type': ['string', 'null']}})}


def validate_assets(state, config):
    """A missing quantity is neither zero nor a spendable quantity."""
    if not config.get('possession_rules'):
        return
    for key, item in state['inventory'].items():
        if item.get('quantity_precision', 'exact') == 'unknown' and item['quantity'] is not None:
            raise ValueError('Unknown quantity with an invented figure: ' + key)
        if item['quantity'] is None and item.get('quantity_precision') != 'unknown':
            raise ValueError('Zero quantity without an unknown precision: ' + key)
        for field in ('location', 'condition', 'possession_status', 'quantity_precision', 'unit'):
            if field not in item:
                raise ValueError('Possession metadata missing: ' + key + ':' + field)
        if item['equipped'] and item['location'] != 'on:' + item['owner_id']:
            raise ValueError('Equipped item outside its carrier: ' + key + ' (location expected: on:' + item['owner_id'] + ')')
    for resource in config['possession_rules'].get('currencies', {}):
        if resource not in state['resources']:
            raise ValueError('Money counter missing: ' + resource)


def materialize(state, event, config):
    """Produce the changes from the operations, without modifying the provided state."""
    operations = event.get('asset_operations', [])
    validate(operation_schema(), operations)
    if not operations:
        return deepcopy(event)
    rules = config.get('possession_rules')
    if not rules:
        raise ValueError('Material operations without possession rules')
    from .contracts import load_schema
    item_schema = load_schema('state')['properties']['inventory']['additionalProperties']
    working = deepcopy(state)
    generated = []
    touched = set()
    for op in operations:
        kind, resource, delta = op['kind'], op['money_resource'], op['money_delta']
        inputs, outputs = op['inputs'], op['outputs']
        if kind in ('payment', 'purchase', 'sale'):
            wrong_sign = (delta >= 0 if kind == 'purchase' else delta <= 0 if kind == 'sale' else delta == 0)
            if resource not in rules['currencies'] or wrong_sign:
                raise ValueError('Incorrect currency or payment sign')
        elif resource is not None or delta != 0:
            raise ValueError('Currency forbidden for this material operation')
        if kind == 'payment' and (inputs or outputs):
            raise ValueError('Simple payment with objects')
        if kind in ('purchase', 'acquire') and (inputs or not outputs):
            raise ValueError('Acquisition without goods received or with consumption')
        if kind == 'sale' and (not inputs or outputs):
            raise ValueError('Sale without goods given up')
        if kind == 'craft' and (not inputs or not outputs):
            raise ValueError('Crafting without materials or result')
        if kind == 'consume' and (not inputs or outputs):
            raise ValueError('Consumption without materials or with a result')
        if kind in ('transfer', 'gift') and (len(inputs) != 1 or outputs or not op['destination']):
            raise ValueError('Transfer: a whole lot and a destination are required')
        if kind == 'gift' and not re.fullmatch('on:[^:\\s/]+', op['destination']):
            raise ValueError('Gift: destination on:<new_owner> required')
        if kind not in ('transfer', 'gift') and op['destination'] is not None:
            raise ValueError('Destination reserved for transfer')
        if len({x['item_id'] for x in inputs}) != len(inputs) or len({x['item_id'] for x in outputs}) != len(outputs):
            raise ValueError('Duplicate lot in an operation')
        currency_owner = rules['currencies'][resource]['owner_id'] if resource else None
        if resource:
            balance = working['resources'][resource] + delta
            if balance < 0:
                raise ValueError('Solde insuffisant : ' + resource)
            working['resources'][resource] = balance
            generated.append({'operation': 'increment', 'path': '/resources/' + resource, 'value': delta})
            touched.add('/resources/' + resource)
        for source in inputs:
            key, count = source['item_id'], source['quantity']
            item = working['inventory'].get(key)
            if item is None:
                raise ValueError('Material missing: ' + key)
            if currency_owner and item['owner_id'] != currency_owner:
                raise ValueError('Sale of a good belonging to another owner')
            if kind in ('sale', 'craft', 'consume') and item.get('possession_status') == 'borrowed':
                raise ValueError('Consumption or sale of a borrowed object refused')
            path = '/inventory/' + key
            touched.add(path)
            if kind in ('transfer', 'gift'):
                if count != item['quantity']:
                    raise ValueError('Partial transfer: split the lot before moving it')
                value = deepcopy(item)
                value.update(location=op['destination'], equipped=False)
                if kind == 'gift':
                    if item.get('possession_status') not in ('owns', 'deposited') or item['quantity'] == 0:
                        raise ValueError('Gift without established ownership or empty lot: ' + key)
                    value.update(owner_id=op['destination'].removeprefix('on:'), possession_status='owns')
            else:
                if count is None:
                    if item['quantity'] is not None:
                        raise ValueError('Unquantified consumption of a quantified lot')
                    # null consumes the whole lot, never an invented fraction.
                    value = None
                else:
                    if item['quantity'] is None or item.get('quantity_precision') != 'exact':
                        raise ValueError('Count the lot in play before a numbered consumption: ' + key)
                    if count > item['quantity']:
                        raise ValueError('Stock insuffisant : ' + key)
                    value = deepcopy(item)
                    value['quantity'] -= count
                    if value['quantity'] == 0:
                        # An exhausted lot (meal, drink, material) leaves the active inventory;
                        # the event log keeps the trace.
                        value = None
            if value is None:
                del working['inventory'][key]
                generated.append({'operation': 'remove', 'path': path, 'value': None})
            else:
                working['inventory'][key] = value
                generated.append({'operation': 'set', 'path': path, 'value': value})
        for output in outputs:
            key = output['item_id']
            if '/' in key:
                raise ValueError('Incorrect lot identifier')
            value = json.loads(output['item_json'])
            check_json(value)
            validate(item_schema, value)
            # An NPC's carrier is written on:<owner_id> (on:merchant), never on:npc:merchant.
            if str(value.get('location', '')).startswith('on:npc:'):
                value['location'] = 'on:' + value['location'][len('on:npc:'):]
            if value['quantity'] == 0:
                raise ValueError('Acquisition of an empty lot refused')
            if currency_owner and value['owner_id'] != currency_owner:
                raise ValueError('Purchase without attribution to the payer')
            existing = working['inventory'].get(key)
            if existing and existing['quantity'] != 0:
                raise ValueError('Existing lot: use a new identifier to avoid an overwrite')
            working['inventory'][key] = value
            path = '/inventory/' + key
            touched.add(path)
            generated.append({'operation': 'set', 'path': path, 'value': value})
    if any(change['path'] in touched for change in event['changes']):
        raise ValueError('Manual modification duplicating a material operation')
    validate_assets(working, config)
    return {**deepcopy(event), 'changes': [*deepcopy(event['changes']), *generated]}


def _plain(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text.casefold())
                   if unicodedata.category(c) != 'Mn')


def _positive(text):
    text = _plain(text)
    text = re.sub(("\\b(?:no|none|nothing|not|without|never|remain\\w*|must|shall|will|would|should|cannot|can "
                   "not|can't|don't|doesn't|won't)\\b[^.;\\n]*"), '', text)
    text = re.sub(('\\b(?:receives?|received|gets?|got) (?:(?:an?|the|some|any) '
                   ')?(?:information|confirmation|answer|response|reply|message|offer|proposal|quote)\\b'), '', text)
    return re.sub(('\\b(?:reports?|reported|brings?|brought) (?:(?:your|his|her|their|the|an?|some) '
                   ')?(?:observations?|information|news|intelligence|report|answer|response)\\b'), '', text)


def _goods_claim(text):
    # Receiving information or a report moves no goods.
    return any(re.search(r'\b(?:buy|buys|bought|craft|crafts|crafted|consume|consumes|consumed|bring|brings|brought|fetch|fetches|fetched)\b', sentence)
               and re.search(r'\b(?:wood|planks?|posts?|beams?|stones?|nails?|fittings?|weapons?|tools?|objects?|items?|lots?|plates?|letters?|materials?)\b', sentence)
               for sentence in re.split(r'[.;\n]', text))


def validate_transition(before, after, event, config):
    """Structural checks and explicit announcements, without claiming to understand every story."""
    if not config.get('possession_rules'):
        return
    validate_assets(after, config)
    operations = event.get('asset_operations', [])
    currencies = config['possession_rules']['currencies']
    expected = {key: 0 for key in currencies}
    for op in operations:
        if op['money_resource']:
            expected[op['money_resource']] += op['money_delta']
    for key in currencies:
        if after['resources'].get(key) != before['resources'].get(key, 0) + expected[key]:
            raise ValueError('Monetary change without an explicit operation: ' + key)
    for key in before.get('projects', {}):
        if key not in after.get('projects', {}):
            raise ValueError('A project must be archived, not deleted: ' + key)
        old = before['projects'][key]
        new = after['projects'][key]
        if set(old['completed']) - set(new['completed']):
            raise ValueError('Acquired step erased from the project: ' + key)
    changed_items = {key for key in before['inventory'].keys() | after['inventory'].keys()
                     if before['inventory'].get(key) != after['inventory'].get(key)}
    operated = {item['item_id'] for op in operations for item in op['inputs'] + op['outputs']}
    for key in changed_items - operated:
        old, new = before['inventory'].get(key), after['inventory'].get(key)
        if old is not None and new is None and old['quantity'] == 0:
            continue  # Cleanup of an already exhausted lot, with no goods moved.
        if old is None or new is None:
            raise ValueError('Acquisition or removal without a material operation: ' + key)
        if old['owner_id'] != new['owner_id'] or old.get('location') != new.get('location'):
            raise ValueError('Transfer without a material operation: ' + key)
        if old['quantity'] != new['quantity']:
            count_in_game = (old.get('quantity_precision') in ('unknown', 'approximate')
                             and new.get('quantity_precision') == 'exact'
                             and re.search(r'\b(?:counts?|counted|tallies|tallied|inventoried)\b', _plain(event['description'])))
            if not count_in_game:
                raise ValueError('Quantity changed without an operation or an in-game count: ' + key)
    positive = _positive(event['description'])
    amount_words = ('(?:\\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|and|[a-z]+-[a-z]+)')
    payment_claim = re.search(('\\b(?:pays?|paid|receives?|received|collects?|collected|spends?|spent|settles?|settled)\\b\\s+(?:(?:the|some|his|her|their|your)\\s+)?(?:') + amount_words + r'\s+)*(?:pieces?|coins?|currency|gold|silver|copper)\b', positive)
    if payment_claim and not any(op['money_delta'] for op in operations):
        raise ValueError('Payment announced without a monetary operation')
    goods_claim = _goods_claim(positive)
    if goods_claim and not changed_items:
        raise ValueError('Movement of goods announced without an inventory update')


def validate_narration(before, after, proposal, config):
    """Also spot material announcements made only in the displayed text."""
    if not config.get('possession_rules'):
        return
    operations = [op for event in proposal['events'] for op in event.get('asset_operations', [])]
    from .narrative_coherence import assertions
    text = '\n'.join(assertions(proposal['narration']))
    positive = _positive(text)
    if re.search(r'\byou (?:pay|pays|spend|spends|collect|collects)\b', positive) and not any(op['money_delta'] for op in operations):
        raise ValueError('Expense or gain in the narration without a monetary operation')
    if _goods_claim(positive) and before['inventory'] == after['inventory']:
        raise ValueError('Goods announced in the narration without an inventory change')
    for key, currency in config['possession_rules']['currencies'].items():
        # Check current numbered balances, without interpreting historical quotations.
        if currency['owner_id'] == 'player':
            denomination = _plain(currency['label'])
            for balance in re.findall('\\byou (?:have|now have|currently have|own)\\s+(\\d+)\\s+' + re.escape(denomination), text):
                if int(balance) != after['resources'][key]:
                    raise ValueError('Announced balance differs from the persistent purse')


def packet(state, config, recipient='player'):
    currencies = config.get('possession_rules', {}).get('currencies', {})
    money = {key: {**value, 'balance': state['resources'][key]}
             for key, value in currencies.items()
             if recipient == 'narrator' or recipient == ('player' if value['owner_id'] == 'player' else 'npc:' + value['owner_id'])}
    return {'status': 'inventory', 'purse': money,
            'possessions': {key: deepcopy(value) for key, value in state['inventory'].items() if recipient in value['audience']},
            'projects': {key: deepcopy(value) for key, value in state.get('projects', {}).items() if recipient in value['audience']}}
