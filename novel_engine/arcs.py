"""Prospective registry of the arcs, installed explicitly, without importing the past."""
import hashlib
import json
import re
import unicodedata

from .contracts import check_json

DAY = 1440
REVIEW_DELAY = 2 * DAY
# Long activity requested by the player: extended review delay for this single chronicle turn.
LONG_REVIEW_DELAY = 7 * DAY
MAX_OBSTACLES = 2
TERMINAL = {'achieved', 'prevented', 'diverted'}
STATUSES = ['to_prepare', 'in_progress', 'achieved', 'prevented', 'diverted']
# Saves made before the rename keep their arcs readable.
OLD_STATUSES = {'a_prepare': 'to_prepare'}


def decision_schema(refs):
    properties = {
        'id': {'type': 'string', 'maxLength': 80},
        'expected_revision': {'type': 'integer', 'minimum': 0},
        'status': {'type': 'string', 'enum': STATUSES},
        'step': {'type': 'integer', 'minimum': 0, 'maximum': 20},
        'motif': {'type': 'string', 'enum': ['progression', 'obstacle', 'dependency', 'divergence']},
        'reason': {'type': 'string', 'minLength': 10, 'maxLength': 600},
        'world_fact': {'type': ['string', 'null'], 'minLength': 10, 'maxLength': 900},
        'next_review': {'type': ['integer', 'null'], 'minimum': 0},
        'references': {'type': 'array', 'minItems': 1, 'maxItems': 8,
                       'items': {'type': 'string', 'enum': sorted(refs) or ['no_source_available']}},
    }
    return {'type': 'array', 'maxItems': 24, 'items': {
        'type': 'object', 'properties': properties,
        'required': list(properties), 'additionalProperties': False}}


class ArcRegistry:
    def __init__(self, con):
        self.con = con

    def exists(self):
        return self.con.execute("SELECT 1 FROM sqlite_master WHERE name='chronicle_arc_meta'").fetchone() is not None

    @staticmethod
    def verify(plan, nodes):
        """The plan is a campaign choice; the sources stay immutable."""
        if plan.get('version') != 1 or not 1 <= len(plan.get('arcs', [])) <= 24:
            raise ValueError('Invalid arcs plan.')
        arcs = {a['id']: a for a in plan['arcs']}
        if len(arcs) != len(plan['arcs']):
            raise ValueError('Duplicate arc identifier.')
        covered = set()
        for a in arcs.values():
            if not re.fullmatch('[a-z0-9_.-]{1,80}', a['id']):
                raise ValueError('Invalid arc identifier.')
            if not a['title'] or not 1 <= len(a['steps']) <= 20 or not all(a['steps']):
                raise ValueError('Missing steps: ' + a['id'])
            if not a['nodes'] or set(a['nodes']) - nodes:
                raise ValueError('Chronology reference missing: ' + a['id'])
            covered.update(a['nodes'])
            if not 0 <= a['opening_days'] < a['end_days'] <= 365:
                raise ValueError('Invalid campaign window: ' + a['id'])
            if set(a['dependencies']) - arcs.keys():
                raise ValueError('Unknown dependency: ' + a['id'])
        visiting, done = set(), set()
        def visit(identifier):
            if identifier in visiting:
                raise ValueError('Dependency cycle.')
            if identifier in done:
                return
            visiting.add(identifier)
            for dep in arcs[identifier]['dependencies']:
                visit(dep)
            visiting.remove(identifier)
            done.add(identifier)
        for identifier in arcs:
            visit(identifier)
        # Every episode has an arc, or an explicitly justified editorial exclusion.
        excluded = plan.get('outside_arcs', {})
        if set(excluded) - nodes or not all(excluded.values()):
            raise ValueError('Invalid documentary exclusion.')
        if nodes - covered - excluded.keys():
            raise ValueError('Episodes without follow-up: ' + ', '.join(sorted(nodes - covered - excluded.keys())))
        return arcs

    def install(self, plan, nodes, minute):
        definitions = self.verify(plan, nodes)
        signature = hashlib.sha256(check_json(plan).encode()).hexdigest()
        if self.exists():
            if self.con.execute('SELECT signature FROM chronicle_arc_meta').fetchone()[0] != signature:
                raise ValueError('A different registry is already installed; no implicit replacement.')
            return 0
        self.con.execute('CREATE TABLE chronicle_arc_meta (id INTEGER PRIMARY KEY CHECK(id=1),signature TEXT NOT NULL,installed_minute INTEGER NOT NULL)')
        self.con.execute('CREATE TABLE chronicle_arcs (id TEXT PRIMARY KEY,revision INTEGER NOT NULL,payload TEXT NOT NULL)')
        self.con.execute('CREATE TABLE chronicle_arc_history (id TEXT NOT NULL,revision INTEGER NOT NULL,review_id TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(id,revision))')
        self.con.execute('CREATE TABLE chronicle_arc_facts (reference TEXT PRIMARY KEY,arc_id TEXT NOT NULL,world_minute INTEGER NOT NULL,payload TEXT NOT NULL)')
        self.con.execute('INSERT INTO chronicle_arc_meta VALUES (1,?,?)', (signature, minute))
        for a in definitions.values():
            item = {**a, 'status': 'to_prepare', 'step': 0, 'obstacle_postponements': 0,
                    'opening_minute': minute + a['opening_days'] * DAY,
                    'end_minute': minute + a['end_days'] * DAY,
                    'next_review': minute + a['opening_days'] * DAY,
                    'reason': 'Prospective campaign placement, canonical dates not deduced.'}
            self._put(item, 'installation', 0)
        return len(definitions)

    def _put(self, item, review_id, revision):
        raw = check_json(item)
        self.con.execute('INSERT OR REPLACE INTO chronicle_arcs VALUES (?,?,?)', (item['id'], revision, raw))
        self.con.execute('INSERT INTO chronicle_arc_history VALUES (?,?,?,?)', (item['id'], revision, review_id, raw))

    def entries(self):
        if not self.exists():
            return []
        entries = [{**json.loads(r['payload']), 'revision': r['revision'],
                    'reference': 'arc:' + r['id'] + ':' + str(r['revision'])}
                   for r in self.con.execute('SELECT * FROM chronicle_arcs ORDER BY id')]
        for entry in entries:
            entry['status'] = OLD_STATUSES.get(entry['status'], entry['status'])
        return entries

    def horizon(self, minute):
        dates = [a['next_review'] for a in self.entries() if a['status'] not in TERMINAL]
        return max(minute, min(dates)) if dates else None

    def packet(self, minute):
        entries = self.entries()
        return {'arcs': entries,
                'to_process': [a['id'] for a in entries if a['status'] not in TERMINAL and a['next_review'] <= minute],
                'max_review_delay': REVIEW_DELAY, 'obstacle_postponements_max': MAX_OBSTACLES,
                'placement': 'Campaign windows, never established dates of the novel. An unknown date blocks no arc.'}

    def facts(self, limit=12, query=''):
        if not self.exists():
            return []
        # Also recall a named old arc, not only the last twelve facts.
        def words(text):
            plain=''.join(c for c in unicodedata.normalize('NFKD', text.casefold()) if not unicodedata.combining(c))
            return set(re.findall(r'[a-z]{4,}', plain)) - {'with','from','that','this','their','have','there','about','after','when'}
        terms=words(query)
        selected=[a['id'] for a in self.entries() if terms & words(a['title'])]
        if selected:
            marks=','.join('?' for _ in selected)
            return [json.loads(r[0]) for r in self.con.execute(
                'SELECT payload FROM chronicle_arc_facts ORDER BY CASE WHEN arc_id IN ('+marks+
                ') THEN 0 ELSE 1 END,world_minute DESC,rowid DESC LIMIT ?', (*selected, limit))]
        return [json.loads(r[0]) for r in self.con.execute(
            'SELECT payload FROM chronicle_arc_facts ORDER BY world_minute DESC,rowid DESC LIMIT ?', (limit,))]

    def apply(self, review_id, packet, updates, minute, clocks, waiting=frozenset()):
        known = {a['id']: a for a in packet['arcs_registry']['arcs']}
        # Review delay extended only by a long routine that asked for it; otherwise two days.
        delay = packet['arcs_registry'].get('max_review_delay', REVIEW_DELAY)
        if type(delay) is not int or not REVIEW_DELAY <= delay <= 30 * DAY:
            raise ValueError('Invalid arc review delay.')
        current = {a['id']: a for a in self.entries()}
        if current != known:
            raise ValueError('Arc registry obsolete.')
        ids = [u['id'] for u in updates]
        if len(set(ids)) != len(ids) or set(ids) - known.keys():
            raise ValueError('Unknown arc or modified twice.')
        if set(packet['arcs_registry']['to_process']) - set(ids):
            raise ValueError('Arc reached its deadline without a decision.')
        allowed = set(packet['allowed_references'])
        proofs = {p['reference'] for p in packet.get('campaign_proofs', [])}
        # A revision does not realize a whole causal chain at once.
        for u in updates:
            old = known[u['id']]
            if old['status'] in TERMINAL or u['expected_revision'] != old['revision']:
                raise ValueError('Obsolete arc revision or finished arc.')
            if minute < old['opening_minute']:
                raise ValueError('Arc before its campaign window.')
            if set(u['references']) - allowed:
                raise ValueError('Arc source not provided.')
            terminal = u['status'] in TERMINAL
            if terminal != (u['next_review'] is None):
                raise ValueError('A next review is required exactly for open arcs.')
            if not terminal and not minute < u['next_review'] <= minute + delay:
                raise ValueError('Arc review required within %d fictional days.' % (delay // DAY))
            deps = [d for d in old['dependencies'] if known[d]['status'] not in TERMINAL]
            step = u['step'] - old['step']
            if step not in (0, 1):
                raise ValueError('A single arc milestone per revision.')
            if u['step'] > len(old['steps']):
                raise ValueError('Non-existent arc milestone.')
            if u['motif'] == 'progression':
                if deps or step != 1 or not u['world_fact'] or u['status'] not in ('in_progress', 'achieved'):
                    raise ValueError('Progress without a fact, without a milestone or before its dependencies.')
                if (u['status'] == 'achieved') != (u['step'] == len(old['steps'])):
                    raise ValueError('The last milestone realizes the arc, the others keep it in progress.')
                # A linked clock keeps its interrupting event; do not double it off-screen.
                if u['status'] == 'achieved' and old.get('clock'):
                    clock = clocks.get(old['clock'])
                    # The resolution may replace the references of the current payload.
                    # The origin of the clock stays in the first agenda revision.
                    rows = self.con.execute("""SELECT h.payload FROM world_agenda w JOIN agenda_history h
                        ON h.entry_id=w.entry_id AND h.revision=0
                        WHERE w.status IN ('done','finished') AND w.entry_id LIKE 'chronicle:%'""").fetchall()
                    manifested = any(any(ref.startswith('chronicle:' + old['clock'] + ':') for ref in json.loads(r['payload']).get('references', []))
                                     and json.loads(r['payload']).get('interrupt') for r in rows)
                    if not clock or clock['status'] not in ('burst', 'closed') or not manifested:
                        raise ValueError('Linked arc: wait for the effective resolution of the big event of its clock.')
            elif u['motif'] == 'dependency':
                if not deps or step or terminal or u['world_fact'] or u['status'] != old['status']:
                    raise ValueError('Wait without a really open dependency.')
                # The root must advance; the acyclic graph prevents a circular wait.
            elif u['motif'] == 'obstacle':
                if step or terminal or u['world_fact'] or u['status'] != old['status']:
                    raise ValueError('Obstacle incompatible with a realization.')
                reason = ''.join(c for c in unicodedata.normalize('NFKD', u['reason'].casefold()) if not unicodedata.combining(c))
                if re.search(('date.{0,25}(unknown|uncertain|missing)|lack of '
                              'information|(?:player|character).{0,15}absent'), reason):
                    raise ValueError('An unknown date or the absence of the player character does not block an arc.')
                # Waiting for the restored big event of the linked clock: neither proof nor ceiling on postponements.
                if old.get('clock') in waiting:
                    pass
                elif old['obstacle_postponements'] >= MAX_OBSTACLES or not proofs.intersection(u['references']):
                    raise ValueError('Obstacle without campaign proof or postponements exhausted.')
            elif u['motif'] == 'divergence':
                if u['status'] not in ('prevented', 'diverted') or step or not u['world_fact'] or not proofs.intersection(u['references']):
                    raise ValueError('Divergence without a campaign cause or a recorded consequence.')
            if old['status'] == 'in_progress' and u['status'] == 'to_prepare':
                raise ValueError('An engaged arc does not return to preparing.')
        for u in updates:
            old = known[u['id']]
            item = {k: v for k, v in old.items() if k not in ('revision', 'reference')}
            item.update({k: u[k] for k in ('status', 'step', 'next_review', 'reason')})
            item['obstacle_postponements'] = old['obstacle_postponements'] + (u['motif'] == 'obstacle' and old.get('clock') not in waiting) if u['motif'] == 'obstacle' else (0 if u['motif'] == 'progression' else old['obstacle_postponements'])
            # An overrun window stays an explicit delay, never a removal of the arc.
            item['window_exceeded'] = minute > old['end_minute']
            item['last_decision'] = u
            revision = old['revision'] + 1
            self._put(item, review_id, revision)
            if u['world_fact']:
                ref = 'arc_fact:' + old['id'] + ':' + str(revision)
                fact = {'reference': ref, 'arc_id': old['id'], 'world_minute': minute,
                        'description': u['world_fact'], 'references': u['references'],
                        'audience': ['narrator'], 'player_knowledge': False}
                self.con.execute('INSERT INTO chronicle_arc_facts VALUES (?,?,?,?)', (ref, old['id'], minute, check_json(fact)))
