"""Durable technical budget per action, with no mutation of the game state."""
import json

from .contracts import check_json
from .memory import words

MAX_GENERATIONS = 2
MAX_SEARCHES = 2


def exists(con, table):
    return con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone() is not None


class RecoveryBudget:
    def __init__(self, con, request_id):
        # Explicit lineage only: two inputs with the same text stay distinct.
        con.execute('''CREATE TABLE IF NOT EXISTS action_recovery_roots (
            request_id TEXT PRIMARY KEY, root_id TEXT NOT NULL)''')
        family = [request_id]
        if exists(con, 'session'):
            row = con.execute('SELECT payload FROM session WHERE id=1').fetchone()
            pending = json.loads(row[0]).get('pending_action') if row else None
            if pending and pending['request_id'] == request_id:
                family = [*pending.get('previous_request_ids', []), request_id]
        mapped = con.execute('SELECT root_id FROM action_recovery_roots WHERE request_id=?', (family[0],)).fetchone()
        root = mapped[0] if mapped else family[0]
        unmapped = [rid for rid in family if not con.execute(
            'SELECT 1 FROM action_recovery_roots WHERE request_id=?',(rid,)).fetchone()]
        self._initialize(con,root)
        total = self.state()
        for rid in unmapped:
            if rid == root: continue
            self._initialize(con,rid)
            additional = self.state()
            total['generations'] += additional['generations']
            total['searches'] += additional['searches']
            total['history'] += additional['history']
        self.con, self.request_id, self.action_request_id = con, root, request_id
        con.execute('UPDATE action_recovery_budget SET generations=?,searches=?,history=? WHERE request_id=?',
                    (total['generations'], total['searches'], check_json(total['history']), root))
        for rid in family:
            con.execute('INSERT OR IGNORE INTO action_recovery_roots VALUES (?,?)',(rid,root))

    def _initialize(self, con, request_id):
        self.con, self.request_id = con, request_id
        con.execute('''CREATE TABLE IF NOT EXISTS action_recovery_budget (
            request_id TEXT PRIMARY KEY, generations INTEGER NOT NULL,
            searches INTEGER NOT NULL, history TEXT NOT NULL)''')
        if not con.execute('SELECT 1 FROM action_recovery_budget WHERE request_id=?', (request_id,)).fetchone():
            # Also take older attempts into account, even after a recall that reset count to zero.
            generations = 0
            for table in ('narrative_attempt_history', 'narrative_attempts'):
                if exists(con, table):
                    for row in con.execute('SELECT metrics,status,proposal FROM '+table+' WHERE request_id=?', (request_id,)):
                        metrics = json.loads(row[0])
                        receipt=json.loads(row[2]) if row[2] else {}
                        generations += int(metrics.get('model_calls', 0) >
                                           metrics.get('planner', {}).get('model_calls', 0) or
                                           'received' in receipt or row[1]=='in_progress')
            searches, history = 0, []
            if exists(con, 'narrative_diagnostics'):
                rows = con.execute('SELECT payload FROM narrative_diagnostics WHERE request_id=? ORDER BY id',
                                   (request_id,)).fetchall()
                signatures=set()
                for row in rows:
                    response = json.loads(row[0]).get('response', {})
                    signature=check_json(response)
                    if isinstance(response, str):
                        try: response = json.loads(response)
                        except ValueError: response = {}
                    if isinstance(response,dict) and response:signature=check_json(response)
                    signatures.add(signature)
                    if isinstance(response, dict) and response.get('memory_request'):
                        searches += 1
                        history.append({'query': response['memory_request']['query'], 'references': [],
                                        'progress': None, 'satisfied': False, 'legacy': True})
                # Older formats did not always archive each attempt.
                # An identical revalidation of a receipt does not add a generation.
                generations=max(generations,len(signatures))
            if exists(con, 'memory_recovery'):
                row = con.execute('SELECT count,query,packet FROM memory_recovery WHERE request_id=?',
                                  (request_id,)).fetchone()
                if row:
                    searches = max(searches, row[0])
                    packet = json.loads(row[2])
                    history.append({'query': row[1], 'references': references(packet),
                                    'progress': None, 'satisfied': False, 'legacy': True})
            if exists(con, 'canon_selection_contexts'):
                supplied=set()
                for row in con.execute('SELECT payload FROM canon_selection_contexts WHERE request_id=? AND role=?',
                                       (request_id,'narrator')):
                    supplied.update(references(json.loads(row[0])))
                if supplied:
                    history.append({'query': '', 'references': sorted(supplied), 'progress': None,
                                    'satisfied': False, 'initial_context': True})
            con.execute('INSERT INTO action_recovery_budget VALUES (?,?,?,?)',
                        (request_id, generations, searches, check_json(history)))

    def state(self):
        row = self.con.execute('SELECT generations,searches,history FROM action_recovery_budget WHERE request_id=?',
                               (self.request_id,)).fetchone()
        return {'generations': row[0], 'searches': row[1], 'history': json.loads(row[2])}

    def reserve_generation(self):
        state = self.state()
        if self.generation_exhausted():
            return self.blocked('Generation budget reached for this action.')
        self.con.execute('UPDATE action_recovery_budget SET generations=generations+1 WHERE request_id=?',
                         (self.request_id,))
        return None

    def release_generation(self):
        """Give back a reservation when no generation happened (a request written for a person to answer)."""
        self.con.execute('UPDATE action_recovery_budget SET generations=MAX(generations-1,0) WHERE request_id=?',
                         (self.request_id,))

    def limits(self):
        limits={'generations':MAX_GENERATIONS,'searches':MAX_SEARCHES}
        repairs=[item for item in self.state()['history'] if item.get('repair_id')]
        if repairs:
            version=json.loads(self.con.execute('SELECT state FROM campaign WHERE id=1').fetchone()[0])['state_version']
            for item in repairs:
                if item['state_version']==version:
                    for key in limits:limits[key]=max(limits[key],item['limits'][key])
        return limits

    def generation_exhausted(self):
        return self.state()['generations']>=self.limits()['generations']

    def search_exhausted(self):
        return self.state()['searches']>=self.limits()['searches']

    def blocked(self, reason):
        state = self.state()
        return {'status': 'memory_required', 'request_id': self.action_request_id,
                'search_status': 'blocked', 'diagnostic_code': 'recovery_blocked',
                'reason': reason+(' Action and scene kept. /resume, /reminder and /retry do not reset this budget: '
                                     '`novel unblock` previews granting one more generation.'),
                'recovery_budget': {key: state[key] for key in ('generations', 'searches')}}

    def record_search(self, query, packet, satisfied):
        state = self.state()
        refs = references(packet)
        previous = {ref for item in state['history'] for ref in item['references']}
        new = sorted(set(refs)-previous)
        history = state['history'] + [{'query': query, 'subject': ' '.join(words(query)),
                                      'references': refs, 'new_references': new,
                                      'progress': bool(new), 'satisfied': satisfied}]
        self.con.execute('UPDATE action_recovery_budget SET searches=searches+1,history=? WHERE request_id=?',
                         (check_json(history), self.request_id))
        return new


def references(packet):
    return sorted({item['reference'] for key in ('facts', 'proofs') for item in packet.get(key, [])
                   if 'reference' in item})
