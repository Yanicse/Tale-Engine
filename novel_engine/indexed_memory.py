"""Indexed archives, extractive memos and bounded recovery, with no model."""
import json
import re
import time

from .contracts import check_json
from .memory import memory_terms, memory_scores, words

VERSION = 1
MAX_CANDIDATES = 50
TEXT_BUDGET = 16000
# Default memory topics: (identifier, title, trigger words, facet words). The narrator adds the campaign's own
# topics through memory_updates.
TOPICS = (
    ('magic', 'Confirmed powers and rules',
     ('spells', 'power', 'ability', 'magic', 'ritual'), ('confirmed', 'cost')),
)


def normalized(text):
    return ' '.join(words(text))


class MemoryIndex:
    def __init__(self, con):
        self.con = con

    def exists(self):
        return self.con.execute("SELECT 1 FROM sqlite_master WHERE name='memory_meta'").fetchone() is not None

    def initialize(self):
        # execute, and not executescript: keep the caller's transaction.
        statements = (
            '''CREATE TABLE IF NOT EXISTS memory_meta (
                id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL,
                last_event INTEGER NOT NULL, last_exchange INTEGER NOT NULL, event_count INTEGER NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS memory_sources (
                doc_id INTEGER PRIMARY KEY, reference TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
                text TEXT NOT NULL, seq INTEGER, event_order INTEGER, request_id TEXT,
                world_minute INTEGER, audience TEXT NOT NULL, payload TEXT)''',
            'CREATE INDEX IF NOT EXISTS memory_sources_kind_order ON memory_sources(kind,event_order DESC)',
            'CREATE INDEX IF NOT EXISTS memory_sources_seq ON memory_sources(seq)',
            '''CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                text, content='memory_sources', content_rowid='doc_id',
                tokenize='unicode61 remove_diacritics 2')''',
            '''CREATE TRIGGER IF NOT EXISTS memory_sources_insert AFTER INSERT ON memory_sources BEGIN
                INSERT INTO memory_fts(rowid,text) VALUES (new.doc_id,new.text); END''',
            '''CREATE TABLE IF NOT EXISTS memory_topics (
                topic_id TEXT PRIMARY KEY, title TEXT NOT NULL, facets TEXT NOT NULL,
                audience TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS memory_aliases (
                alias TEXT NOT NULL, topic_id TEXT NOT NULL, PRIMARY KEY(alias,topic_id))''',
            '''CREATE TABLE IF NOT EXISTS memory_links (
                topic_id TEXT NOT NULL, doc_id INTEGER NOT NULL, PRIMARY KEY(topic_id,doc_id))''',
            'CREATE INDEX IF NOT EXISTS memory_links_doc ON memory_links(doc_id,topic_id)',
            '''CREATE TABLE IF NOT EXISTS memory_recovery (
                request_id TEXT PRIMARY KEY, query TEXT NOT NULL, status TEXT NOT NULL,
                count INTEGER NOT NULL, packet TEXT NOT NULL, reason TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS memory_update_receipts (
                request_id TEXT PRIMARY KEY, updates TEXT NOT NULL)''',
        )
        for statement in statements:
            self.con.execute(statement)
        self.con.execute('INSERT OR IGNORE INTO memory_meta VALUES (1,?,0,0,0)', (VERSION,))
        version = self.con.execute('SELECT version FROM memory_meta WHERE id=1').fetchone()[0]
        if version != VERSION:
            raise ValueError('Incompatible memory version; explicit migration needed.')
        for identifier, title, aliases, facets in TOPICS:
            self.topic(identifier, title, aliases, facets, ['narrator','player'])

    def topic(self, identifier, title, aliases, facets=(), audience=None):
        if not re.fullmatch(r'[a-z0-9_:.-]{1,80}', identifier):
            raise ValueError('Incorrect topic identifier')
        self.con.execute('INSERT OR IGNORE INTO memory_topics VALUES (?,?,?,?)',
                         (identifier, title, check_json(list(facets)),check_json(audience or ['narrator'])))
        for alias in (identifier, *aliases):
            term = normalized(alias)
            if term:
                self.con.execute('INSERT OR IGNORE INTO memory_aliases VALUES (?,?)', (term, identifier))

    def topics_for(self, text, limit=3, recipient='narrator'):
        tokens = words(text.replace('_', ' '))[:120]
        terms = sorted({' '.join(tokens[i:i+size]) for size in (1,2,3)
                        for i in range(len(tokens)) if len(tokens[i:i+size]) == size})
        if not terms:
            return []
        marks = ','.join('?' for _ in terms)
        rows = self.con.execute(f'''SELECT a.topic_id,COUNT(*) AS matches FROM memory_aliases a
            JOIN memory_topics t ON t.topic_id=a.topic_id WHERE a.alias IN ({marks})
            AND EXISTS (SELECT 1 FROM json_each(t.audience) WHERE value=?)
            GROUP BY a.topic_id ORDER BY matches DESC,a.topic_id LIMIT ?''',
                                (*terms,recipient,limit)).fetchall()
        return [row['topic_id'] for row in rows]

    def _insert(self, reference, kind, text, audience, seq=None, event_order=None,
                request_id=None, world_minute=None, payload=None):
        cursor = self.con.execute('''INSERT OR IGNORE INTO memory_sources
            (reference,kind,text,seq,event_order,request_id,world_minute,audience,payload)
            VALUES (?,?,?,?,?,?,?,?,?)''',
            (reference,kind,text,seq,event_order,request_id,world_minute,check_json(audience),payload))
        if cursor.rowcount:
            for topic_id in self.topics_for(text,limit=8):
                self.con.execute('INSERT OR IGNORE INTO memory_links VALUES (?,?)', (topic_id,cursor.lastrowid))

    def sync(self):
        if not self.exists():
            self.initialize()
        meta = self.con.execute('SELECT * FROM memory_meta WHERE id=1').fetchone()
        if meta['version'] != VERSION:
            raise ValueError('Incompatible memory version')
        event_order, exchange_seq = meta['last_event'], meta['last_exchange']
        added = 0
        for row in self.con.execute('SELECT rowid,payload FROM events WHERE rowid>? ORDER BY rowid',
                                    (event_order,)).fetchall():
            event = json.loads(row['payload'])
            request_id = next((cause[7:] for cause in event['causes'] if cause.startswith('action:')), None)
            self._insert(event['event_id'],'event',event['description'],event['audience'],
                         event_order=row['rowid'],request_id=request_id,
                         world_minute=event['world_minute'],payload=row['payload'])
            event_order = row['rowid']
            added += 1
        # One primary-key lookup for each new exchange, never a global JOIN.
        for row in self.con.execute('SELECT * FROM exchanges WHERE seq>? ORDER BY seq',
                                    (exchange_seq,)).fetchall():
            exchange_seq = row['seq']
            if row['role'] != 'narrator':
                continue
            if row['message_id'] == 'initial_scene':
                request_id, result = None, {'status':'recorded','world_minute_after':0}
            else:
                request_id = row['message_id'].removesuffix(':narrator')
                result_row = self.con.execute('SELECT result FROM requests WHERE request_id=?',
                                               (request_id,)).fetchone()
                result = json.loads(result_row[0]) if result_row else {}
            if result.get('status') != 'recorded':
                continue
            self._insert('exchange:'+str(row['seq']),'exchange',row['content'],['narrator','player'],
                         seq=row['seq'],request_id=request_id,world_minute=result['world_minute_after'])
        self.con.execute('UPDATE memory_meta SET last_event=?,last_exchange=?,event_count=event_count+? WHERE id=1',
                         (event_order,exchange_seq,added))
        return {'added_events':added,'last_sequence':exchange_seq}

    def rebuild(self):
        """Explicit maintenance: rebuild the projections from the archives."""
        if not self.exists():
            return self.sync()
        links=[tuple(row) for row in self.con.execute('''SELECT l.topic_id,s.reference
            FROM memory_links l JOIN memory_sources s ON s.doc_id=l.doc_id''')]
        self.con.execute('DELETE FROM memory_links')
        self.con.execute('DELETE FROM memory_sources')
        self.con.execute("INSERT INTO memory_fts(memory_fts) VALUES ('rebuild')")
        self.con.execute('UPDATE memory_meta SET last_event=0,last_exchange=0,event_count=0 WHERE id=1')
        delta=self.sync()
        for topic_id,reference in links:
            self.con.execute('''INSERT OR IGNORE INTO memory_links SELECT ?,doc_id
                FROM memory_sources WHERE reference=?''',(topic_id,reference))
        return delta

    @staticmethod
    def _access(recipient):
        return "EXISTS (SELECT 1 FROM json_each(s.audience) WHERE value=?)", (recipient,)

    def search(self, query, recipient='narrator', limit=MAX_CANDIDATES, topic_id=None):
        terms = sorted(memory_terms(query))[:24]
        if not terms:
            return []
        match = ' OR '.join('"'+term+'"' for term in terms)
        access, args = self._access(recipient)
        topic_sql, topic_args = ('',()) if topic_id is None else (
            ' AND EXISTS (SELECT 1 FROM memory_links l WHERE l.doc_id=s.doc_id AND l.topic_id=?)', (topic_id,))
        return [dict(row) for row in self.con.execute(f'''SELECT s.*,memory_fts.rank AS score
            FROM memory_fts JOIN memory_sources s ON s.doc_id=memory_fts.rowid
            WHERE memory_fts MATCH ? AND {access}{topic_sql}
            ORDER BY memory_fts.rank LIMIT ?''',(match,*args,*topic_args,limit))]

    def _topic_sources(self, topic_id, recipient):
        row = self.con.execute('SELECT * FROM memory_topics WHERE topic_id=?',(topic_id,)).fetchone()
        if not row or recipient not in json.loads(row['audience']):
            return [], None
        selected = {}
        facets = json.loads(row['facets'])
        for facet in facets:
            # Facets are search leads, not assertions.
            matches = self.search(facet,recipient,limit=6,topic_id=topic_id)
            terms = memory_terms(facet)
            matches.sort(key=lambda item:(len(terms & memory_terms(item['text'])), -item['score']),reverse=True)
            if matches:
                selected[matches[0]['doc_id']] = matches[0]
        access,args = self._access(recipient)
        founder = self.con.execute(f'''SELECT s.* FROM memory_links l
            JOIN memory_sources s ON s.doc_id=l.doc_id WHERE l.topic_id=? AND {access}
            ORDER BY s.doc_id LIMIT 1''',(topic_id,*args)).fetchone()
        if founder:
            selected.setdefault(founder['doc_id'],dict(founder))
        for source in self.con.execute(f'''SELECT s.* FROM memory_links l
            JOIN memory_sources s ON s.doc_id=l.doc_id WHERE l.topic_id=? AND {access}
            ORDER BY s.doc_id DESC LIMIT 3''',(topic_id,*args)):
            selected.setdefault(source['doc_id'],dict(source))
        return list(selected.values()),dict(row)

    @staticmethod
    def _excerpt(text, terms, max_chars=1800):
        if len(text) <= max_chars:
            return text
        parts = re.split(r'(?<=[.!?])\s+|\n\n',text)
        ranked = sorted(enumerate(parts),key=lambda item:len(terms & memory_terms(item[1])),reverse=True)
        chosen, size = set(), 0
        for index, part in ranked:
            if size + len(part) + 2 <= max_chars:
                chosen.add(index); size += len(part)+2
        if not chosen:
            # A paragraph without punctuation can carry the answer after its first page.
            matches = [m for m in re.finditer(r'[^\W_]+', text, re.UNICODE)
                       if normalized(m.group()) in terms]
            start = max(0, matches[0].start() - max_chars // 3) if matches else 0
            return text[start:start + max_chars]
        return '\n\n'.join(part for index,part in enumerate(parts) if index in chosen)

    def packet(self, action, previous='', position='', recipient='narrator', query='', recent_seqs=(),
               budget=TEXT_BUDGET, excerpt_chars=1800):
        started = time.perf_counter()
        explicit_topics = self.topics_for(action+' '+query,recipient=recipient)
        topic_ids = explicit_topics or self.topics_for(previous+' '+position,recipient=recipient)
        selected, files, origins = {}, [], {}
        exact_refs = set()
        def remember(source, origin):
            selected.setdefault(source['doc_id'], source)
            origins.setdefault(source['doc_id'], set()).add(origin)
        # A technical request can directly designate a proof provided to the model.
        for ref in re.findall(r'exchange:\d+|[a-zA-Z0-9_:.-]{8,}',query):
            row=self.con.execute('SELECT * FROM memory_sources WHERE reference=?',(ref,)).fetchone()
            # Try the exact value first, then remove the final punctuation.
            if row is None and ref.rstrip('.,:') != ref:
                row=self.con.execute('SELECT * FROM memory_sources WHERE reference=?',
                                     (ref.rstrip('.,:'),)).fetchone()
            if row and recipient in json.loads(row['audience']):
                exact_refs.add(row['reference'])
                remember(dict(row), 'requested_reference')
        for topic_id in topic_ids[:3]:
            sources, topic = self._topic_sources(topic_id,recipient)
            if topic and sources:
                files.append({'id':topic_id,'title':topic['title'],
                                 'nature':'Extractive memo; the quotations and their caveats are authoritative.',
                                 'references':[source['reference'] for source in sources]})
                for source in sources:
                    remember(source, 'file:'+topic_id)
        search_text = action+' '+query
        if not memory_terms(search_text):
            search_text = previous
        queries = [search_text]
        if previous and previous != search_text:
            queries.append(search_text+' '+previous)
        candidates = {}
        for search_query in queries[:2]:
            for source in self.search(search_query,recipient):
                candidates[source['doc_id']] = source
        scores = memory_scores([source['text'] for source in candidates.values()],action+' '+query,previous)
        ranked = sorted(zip(scores,candidates.values()),key=lambda pair:(pair[0],pair[1]['doc_id']),reverse=True)
        for _,source in ranked[:10]:
            remember(source, 'search')
        access,args = self._access(recipient)
        for source in self.con.execute(f'''SELECT s.* FROM memory_sources s
            WHERE s.kind='event' AND {access} ORDER BY s.event_order DESC LIMIT 6''',args):
            remember(dict(source), 'recent')
        # The file helps to find; its origin must not displace a precise answer.
        pool = {**candidates, **selected}
        relevance = dict(zip(pool, memory_scores([s['text'] for s in pool.values()], action+' '+query, previous)))
        ordered = sorted(selected.values(), key=lambda s: (
            s['reference'] in exact_refs, relevance[s['doc_id']], s['doc_id']), reverse=True)
        terms = memory_terms(action+' '+previous+' '+query)
        facts,exchanges,proofs = [],[],[]
        used, truncated = 0, False
        decisions = {s['reference']:{'reference':s['reference'], 'origins':sorted(origins.get(i, {'search'})),
            'score':round(relevance[i], 6), 'status':'discarded', 'reason':'rank_search'} for i,s in pool.items()}
        kept_sources = []
        for source in ordered:
            text = self._excerpt(source['text'],terms,max_chars=excerpt_chars)
            decision = decisions[source['reference']]
            decision.update(source_characters=len(source['text']), excerpt_characters=len(text),
                            partial_excerpt=text!=source['text'])
            if used + len(text) > budget:
                truncated = True
                decision['reason'] = 'memory_budget'
                continue
            used += len(text)
            decision.update(status='kept', reason='requested_reference' if source['reference'] in exact_refs else 'relevance')
            kept_sources.append(source)
            ref = source['reference']
            proofs.append({'reference':ref,'text':text,'minute':source['world_minute'],
                           'type':source['kind'],'audience':json.loads(source['audience']),
                           'partial_excerpt':text!=source['text']})
            if source['kind']=='event':
                facts.append({'id':ref,'text':text,'category':'fact','references':[ref],
                              'audience':json.loads(source['audience'])})
            elif source['seq'] not in recent_seqs:
                exchanges.append({'seq':source['seq'],'role':'narrator','content':text})
        # Neighbors only use the budget left after the main proofs.
        added_seqs = set(recent_seqs) | {e['seq'] for e in exchanges}
        neighbors = []
        for source in kept_sources:
            if source['kind']=='exchange' and source['seq'] not in recent_seqs:
                seq = source['seq']
                before = self.con.execute('SELECT seq,role,content FROM exchanges WHERE seq=?',(seq-1,)).fetchone()
                after = self.con.execute("SELECT * FROM memory_sources WHERE seq=? AND kind='exchange'",(seq+2,)).fetchone()
                for neighbor in ([dict(before)] if before and before['role']=='player' else []) + (
                    [{'seq':after['seq'],'role':'narrator','content':after['text']}]
                    if after and recipient in json.loads(after['audience']) else []):
                    if neighbor['seq'] in added_seqs: continue
                    fits = used + len(neighbor['content']) <= budget
                    neighbors.append({'sequence':neighbor['seq'], 'reason':'neighbor' if fits else 'memory_budget',
                                      'status':'kept' if fits else 'discarded'})
                    if fits:
                        exchanges.append(neighbor); used += len(neighbor['content']); added_seqs.add(neighbor['seq'])
                    else: truncated = True
        kept_refs = {proof['reference'] for proof in proofs}
        for file in files:
            file['references'] = [ref for ref in file['references'] if ref in kept_refs]
        # Deduplicate the neighbors and the sources already recalled.
        exchanges = sorted({item['seq']:item for item in exchanges}.values(),key=lambda item:item['seq'])
        return {'facts':facts,'exchanges':exchanges,'files':files,'proofs':proofs,
                'total_facts':self.con.execute('SELECT event_count FROM memory_meta WHERE id=1').fetchone()[0],
                'search':{'subjects':topic_ids,'candidates':len(candidates),'characters':used,
                            'budget':budget,'limit_reached':truncated,
                            'diagnostic':{'sources':list(decisions.values()), 'neighbors':neighbors,
                                          'kept_references':sorted(kept_refs)},
                            'seconds':round(time.perf_counter()-started,6)}}

    def journal_for(self, packet):
        refs = [item['id'] for item in packet['facts']]
        if not refs:
            return []
        marks = ','.join('?' for _ in refs)
        return [json.loads(row[0]) for row in self.con.execute(
            f'SELECT payload FROM memory_sources WHERE reference IN ({marks}) AND payload IS NOT NULL',refs)]

    def validate_updates(self, updates, allowed, proposal):
        """Subjects group exact sources; they do not rewrite the facts."""
        current = {'current_turn'} | {event['event_id'] for event in proposal['events']}
        for update in updates:
            refs = update['references']
            if not refs or any(ref not in allowed | current for ref in refs):
                raise ValueError('Memo without an authorized source refused')
            for ref in refs:
                if ref not in current and not self.con.execute(
                        'SELECT 1 FROM memory_sources WHERE reference=?',(ref,)).fetchone():
                    raise ValueError('Memo source without an exact archive refused')
            if not re.fullmatch(r'[a-z0-9_:.-]{1,80}',update['topic_id']):
                raise ValueError('Incorrect memo identifier')

    def apply_updates(self, updates, request_id):
        serialized=check_json(updates)
        receipt=self.con.execute('SELECT updates FROM memory_update_receipts WHERE request_id=?',(request_id,)).fetchone()
        if receipt:
            if receipt[0]!=serialized:
                raise ValueError('Different memo update for a request already applied')
            return
        row = self.con.execute('SELECT seq FROM exchanges WHERE message_id=?',(request_id+':narrator',)).fetchone()
        current_ref = 'exchange:'+str(row[0]) if row else None
        for update in updates:
            sources=[]
            for ref in update['references']:
                if ref == 'current_turn':
                    ref = current_ref
                source = self.con.execute('SELECT doc_id,audience FROM memory_sources WHERE reference=?',(ref,)).fetchone()
                if source:
                    sources.append(source)
            if not sources:
                raise ValueError('Memo without a recorded source refused')
            audience=set(json.loads(sources[0]['audience']))
            for source in sources[1:]:
                audience &= set(json.loads(source['audience']))
            self.topic(update['topic_id'],update['title'],update['aliases'],audience=sorted(audience))
            for source in sources:
                self.con.execute('INSERT OR IGNORE INTO memory_links VALUES (?,?)',
                                 (update['topic_id'],source[0]))
        self.con.execute('INSERT INTO memory_update_receipts VALUES (?,?)',(request_id,serialized))

    def recover(self, request_id, query, previous, position):
        row = self.con.execute('SELECT * FROM memory_recovery WHERE request_id=?',(request_id,)).fetchone()
        count = (row['count'] if row else 0) + 1
        packet = self.packet('',previous,position,query=query,budget=TEXT_BUDGET*2,excerpt_chars=6000)
        # Compare the content of the proofs, not the rewordings of the request.
        prior = json.loads(row['packet']) if row else {}
        old = {proof['reference']: proof['text'] for proof in prior.get('proofs', [])}
        new = [proof['reference'] for proof in packet['proofs']
               if old.get(proof['reference']) != proof['text']]
        packet['search']['new_proofs'] = new
        if not packet['proofs'] or (row and not new):
            status = 'blocked' if row and row['status'] == 'in_game_verification' else 'in_game_verification'
            reason = (('The search no longer provides new proof. Use /resume to check the unestablished '
                       'details in play, without asking for the same archives again.') if status != 'blocked' else
                      ('The narrator asks again for archives already provided. Action kept; no new automatic '
                       'generation. /reminder can target another archive; /retry allows a new attempt.'))
        else:
            status = 'ready'
            reason = 'Memories recovered locally. Use /resume to allow a new generation.'
        self.con.execute('INSERT OR REPLACE INTO memory_recovery VALUES (?,?,?,?,?,?)',
                         (request_id,query,status,count,check_json(packet),reason))
        return {'status':'memory_required','request_id':request_id,'reason':reason,
                'recovered_sources':len(packet['proofs']),'search_status':status}
