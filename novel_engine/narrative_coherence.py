"""State links and local factual checks, with no general interpretation of the story."""
import re

from .possessions import _plain
from .prompts import load as load_prompt


INSTRUCTIONS = load_prompt('narrative_coherence.instructions')

PUBLIC_RESOURCES = {'player_hp', 'player_hp_max', 'player_mana', 'player_mana_max'}


def public_resources(config):
    """Vitality and charges of the player character, citable through a narrative link."""
    from .character import charge_resources
    return PUBLIC_RESOURCES | charge_resources(config.get('character_rules'))
UNITS = dict(zip(
    ('zero one two three four five six seven eight nine ten eleven twelve '
     'thirteen fourteen fifteen sixteen seventeen eighteen nineteen').split(), range(20)))
TENS = {'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50, 'sixty': 60,
        'seventy': 70, 'eighty': 80, 'ninety': 90}
WORDS = '|'.join([*UNITS, *TENS, 'hundred', 'thousand', 'and'])
NUMBER = rf'(?:\d{{1,3}}(?:[ ,\u00a0\u202f]\d{{3}})+|\d+|(?:{WORDS})(?:[ -](?:{WORDS}))*)'


def number(text):
    text = text.replace('\u00a0', ' ').replace('\u202f', ' ').replace(',', '')
    if text.replace(' ', '').isdigit():
        return int(text.replace(' ', ''))
    total = current = 0
    for word in text.replace('-', ' ').split():
        if word in UNITS:
            current += UNITS[word]
        elif word in TENS:
            current += TENS[word]
        elif word == 'hundred':
            current = (current or 1) * 100
        elif word == 'thousand':
            total += (current or 1) * 1000
            current = 0
    return total + current


def render_state_links(text, state, config):
    """Expansion before commit; no arbitrary reading of the private state."""
    owners = config.get('resource_owners', {})
    public = public_resources(config)
    currencies = config.get('possession_rules', {}).get('currencies', {})

    def replace(match):
        path = match.group(1)
        if path == '/positions/player':
            value = state['positions'].get('player')
        elif re.fullmatch(r'/resources/[a-zA-Z0-9_:-]+', path):
            key = path.split('/')[-1]
            owner = owners.get(key, currencies.get(key, {}).get('owner_id'))
            if owner != 'player' and not (owner is None and key in public):
                raise ValueError('Narrative link to a private or unknown resource.')
            value = state['resources'].get(key)
        else:
            raise ValueError('Narrative link outside the authorized public values.')
        if type(value) not in (int, str):
            raise ValueError('Narrative value missing or not scalar.')
        return str(value)

    result = re.sub(r'\[\[state:([^\[\]\n]+)\]\]', replace, text)
    if '[[state:' in result:
        raise ValueError('Incomplete narrative link.')
    return result


def assertions(text):
    # Quotations and hypotheses are not played acquisitions.
    text = re.sub(r'«[^»]*»|"[^"\n]*"|“[^”]*”', '', text)
    for clause in re.split(r'[.;!?\n]', _plain(text)):
        clause = clause.strip()
        if re.search(r'\b(?:remember\w*|yesterday|formerly|previously|used to)\b', clause):
            continue
        if re.match(r'(?:if|when|whenever|in case)\b', clause):
            continue
        yield clause


def validate_facts(before, after, proposal, config):
    """Check explicit amounts and balances even without public_consequences."""
    actor = r'(?:you'
    if config.get('player_name'):
        actor += '|' + re.escape(_plain(config['player_name']))
    actor += ')'
    balance = (rf'(?:{actor} (?:have|has|own|owns)'
               r'(?: now| still| currently| in total)?|your purse (?:contains|holds))')
    clauses = list(assertions(proposal['narration']))
    currencies = {key: spec for key, spec in config.get('possession_rules', {}).get('currencies', {}).items()
                  if spec['owner_id'] == 'player'}
    operations = [op for event in proposal['events'] for op in event.get('asset_operations', [])]
    inventory = {**before.get('inventory', {}), **after.get('inventory', {})}
    acquisition = rf'\b{actor} (?:buy|buys|bought|receive|receives|received|get|gets|got|obtain|obtains|obtained|bring|brings|brought)\s+([^,]+)'
    for clause in clauses:
        for match in re.finditer(acquisition, clause):
            wording = match.group(1)
            if re.match(r'(?:(?:an?|the|some|any|your)\s+)?(?:information|advice|messages?|offers?|proposals?|answers?|responses?|quotes?)\b', wording):
                continue
            for key in inventory:
                label = re.escape(_plain(key).replace('_', ' '))
                if not re.search(r'\b' + label + r'\b', wording):
                    continue
                received = any(any(output['item_id']==key for output in op['outputs'])
                               or (op['kind'] in ('transfer','gift') and op['destination']=='on:player'
                                   and any(part['item_id']==key for part in op['inputs']))
                               for op in operations)
                if not received:
                    raise ValueError('Narrative acquisition without an operation on this item: ' + key)
    for key, spec in currencies.items():
        labels = [re.escape(_plain(spec['label'])).replace('coins', 'coins?')]
        if len(currencies) == 1:
            labels.append(r'coins?(?!\w)')
        unit = '(?:' + '|'.join(labels) + ')'
        for clause in clauses:
            for match in re.finditer(rf'\b{balance}\s+({NUMBER})\s+{unit}\b', clause):
                if number(match.group(1)) != after['resources'][key]:
                    raise ValueError('Narrative balance differs from the persistent purse: ' + key)
            for sign, verbs in ((-1, 'pay|pays|paid|spend|spends|spent'),
                                (1, 'receive|receives|received|collect|collects|collected|earn|earns|earned')):
                pattern = rf'\b{actor}\s+(?:{verbs})\s+({NUMBER})\s+{unit}\b'
                for match in re.finditer(pattern, clause):
                    amount = number(match.group(1))
                    values = [abs(op['money_delta']) for op in operations
                              if op['money_resource'] == key and op['money_delta'] * sign > 0]
                    if not values or amount not in [*values, sum(values)]:
                        raise ValueError('Amount or direction of the narrative payment without a matching operation.')
    if 'player_hp' in after['resources']:
        for clause in clauses:
            patterns = [rf'\b{balance}\s+({NUMBER})\s+(?:hp|hit points)\b',
                        rf'\byour (?:hp|hit points) (?:are|go|rise|fall|drop|climb) (?:to|back to)\s+({NUMBER})\b']
            for pattern in patterns:
                for match in re.finditer(pattern, clause):
                    maximum = re.match(r'\s+(?:maximum|max)\b', clause[match.end():])
                    key = 'player_hp_max' if maximum else 'player_hp'
                    if number(match.group(1)) != after['resources'].get(key):
                        raise ValueError('Announced HP differ from the persistent HP.')
