"""Additive registry of sourced views, with no implicit effect on the world."""
import json
import re

from .contracts import check_json
from .indexed_memory import MemoryIndex, normalized
from .validation import validate


def explicit_acceptance(action):
    text=normalized(action)
    if '?' in action or re.search(('\\b(?:not|never|refuse\\w*|hesitat\\w*|if|unless|condition\\w*|maybe|perhaps|possibly)\\b|\\bdon '
                                   't accept|\\bnot sure\\b'),text):
        return False
    return text in ('yes','i agree') or bool(re.search(
        ('\\b(?:i accept\\w*|i (?:accept\\w*|agree\\w*|commit\\w*|take (?:the|this|your) '
         '(?:mission|quest|offer)))\\b'),text))


def updates_schema():
    string = {'type':'string', 'minLength':1, 'maxLength':800}
    strings = {'type':'array', 'maxItems':12, 'items':string}
    return {'type':'array', 'maxItems':16, 'items':{'type':'object', 'properties':{
        'entity_id':{'type':'string','pattern':'^[a-z0-9_:.-]{1,64}$'},
        'name':{'type':'string','minLength':1,'maxLength':160},
        'aliases':strings, 'kind':{'type':'string','enum':['npc','equipped','place','quest','object','reputation','detail']},
        'origin':{'type':'string','const':'campaign'},
        'status':{'type':'string','enum':['established','testimony','intention','proposed','accepted','resolved','refused','abandoned']},
        'traits':strings, 'unknowns':strings, 'links':strings,
        'audience':{'type':'array','minItems':1,'maxItems':2,
                    'items':{'type':'string','enum':['narrator','player']}},
        'references':{'type':'array','minItems':1,'maxItems':12,'items':string}},
        'required':['entity_id','name','aliases','kind','origin','status','traits','unknowns','links','audience','references'],
        'additionalProperties':False}}


class CreationRegistry:
    def __init__(self, con):
        self.con = con

    def exists(self):
        return self.con.execute("SELECT 1 FROM sqlite_master WHERE name='creation_revisions'").fetchone() is not None

    def initialize(self):
        self.con.execute('''CREATE TABLE IF NOT EXISTS creation_revisions (
            seq INTEGER PRIMARY KEY, entity_id TEXT NOT NULL, request_id TEXT NOT NULL,
            payload TEXT NOT NULL, UNIQUE(entity_id,request_id))''')
        self.con.execute('CREATE INDEX IF NOT EXISTS creation_identity ON creation_revisions(entity_id,seq DESC)')
        self.con.execute('''CREATE TABLE IF NOT EXISTS creation_receipts (
            request_id TEXT PRIMARY KEY, payload TEXT NOT NULL)''')

    def current(self, entity_id):
        if not self.exists(): return None
        row = self.con.execute('SELECT payload FROM creation_revisions WHERE entity_id=? ORDER BY seq DESC LIMIT 1',
                               (entity_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def recorded(self, updates, request_id):
        if not self.exists(): return False
        receipt=self.con.execute('SELECT payload FROM creation_receipts WHERE request_id=?',(request_id,)).fetchone()
        if not receipt: return False
        if receipt[0]!=check_json(updates): raise ValueError('Different creations when replaying the receipt.')
        return True

    def validate(self, updates, proposal, allowed=None):
        validate(updates_schema(), updates)
        if proposal.get('request_id') and self.recorded(updates,proposal['request_id']):
            return
        if updates and proposal['blocking_questions']:
            raise ValueError('A question forbids creations.')
        if len({u['entity_id'] for u in updates}) != len(updates):
            raise ValueError('Creative identity repeated within the turn.')
        current = {e['event_id']:e['audience'] for e in proposal['events']}
        for u in updates:
            if len(set(u['audience'])) != len(u['audience']) or 'narrator' not in u['audience']:
                raise ValueError('A single creative audience including the narrator is required.')
            old = self.current(u['entity_id'])
            if old and any(old[key] != u[key] for key in ('kind','origin','audience')):
                raise ValueError('Type, origin and view audience are immutable; create a distinct revelation.')
            for ref in u['references']:
                if allowed is not None and ref not in allowed | {'current_turn'} | set(current):
                    raise ValueError('Creation source not provided.')
                if ref == 'current_turn': audience = ['narrator','player']
                elif ref in current: audience = current[ref]
                else:
                    row = self.con.execute('SELECT audience FROM memory_sources WHERE reference=?',(ref,)).fetchone()
                    if not row: raise ValueError('Creation without an exact archive.')
                    audience = json.loads(row[0])
                if set(u['audience']) - set(audience):
                    raise ValueError('Creation audience incompatible with its sources.')
            if u['kind']=='quest' and u['status']=='accepted' and (not old or old['status']!='accepted'):
                row = self.con.execute('SELECT payload FROM session WHERE id=1').fetchone()
                # The text comes from the persisted input, never from the model's story.
                session = json.loads(row[0]) if row else {}
                action = (session.get('pending_action') or {}).get('text','')
                if not explicit_acceptance(action):
                    raise ValueError('Quest accepted without an explicit decision by the player.')

    def apply(self, updates, proposal):
        if not updates and not self.exists(): return
        self.initialize()
        rid = proposal['request_id']
        serialized = check_json(updates)
        receipt = self.con.execute('SELECT payload FROM creation_receipts WHERE request_id=?',(rid,)).fetchone()
        if receipt:
            if receipt[0] != serialized: raise ValueError('Different creations when replaying the receipt.')
            return
        index = MemoryIndex(self.con)
        index.sync()
        row = self.con.execute('SELECT seq FROM exchanges WHERE message_id=?',(rid+':narrator',)).fetchone()
        for u in updates:
            value = {**u, 'references':['exchange:'+str(row[0]) if r=='current_turn' and row else r for r in u['references']]}
            if 'current_turn' in value['references']: raise ValueError('Creation without a recorded dialogue.')
            self.con.execute('INSERT INTO creation_revisions(entity_id,request_id,payload) VALUES (?,?,?)',
                             (u['entity_id'],rid,check_json(value)))
            index.apply_updates([{'topic_id':'creation:'+u['entity_id'], 'title':u['name'],
                'aliases':[u['entity_id'],u['name'],*u['aliases']], 'references':value['references']}],
                rid+':creation:'+u['entity_id'])
            self.con.execute('UPDATE memory_topics SET title=? WHERE topic_id=?',
                             (u['name'],'creation:'+u['entity_id']))
        self.con.execute('INSERT INTO creation_receipts VALUES (?,?)',(rid,serialized))

    def latest(self, recipient='narrator'):
        """Current views available, with no budget and no loading of the history."""
        if not self.exists(): return []
        rows = self.con.execute('''SELECT payload FROM creation_revisions r WHERE seq=(
            SELECT MAX(seq) FROM creation_revisions WHERE entity_id=r.entity_id) ORDER BY seq DESC''')
        return [value for row in rows if recipient in (value := json.loads(row[0]))['audience']]

    def packet(self, query='', recipient='narrator', budget=12000):
        terms = set(normalized(query).split())
        candidates = []
        for value in self.latest(recipient):
            identity = normalized(' '.join([value['entity_id'],value['name'],*value['aliases']]))
            score = len(terms & set(identity.split())) * 10 + len(terms & set(normalized(check_json(value)).split()))
            if query and not score: continue
            candidates.append((score,value))
        output, used = [], 0
        for _, value in sorted(candidates, key=lambda pair:pair[0], reverse=True):
            value = dict(value)
            value['links'] = [link for link in value['links'] if
                self.current(link) is None or recipient in self.current(link)['audience']]
            size = len(check_json(value).encode('utf-8'))
            if used+size > budget: continue
            output.append(value); used += size
        return output
