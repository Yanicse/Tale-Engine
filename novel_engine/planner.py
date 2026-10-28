"""Periodic summarizer and chronicler of the world, with no game turn."""
import hashlib
import json
import re
import time
import unicodedata

from .adapters import AwaitingAnswer, ModelUnavailable
from .agenda import Agenda, ACTIVE, RESPONSE_LIMIT, date
from .contracts import ASSETS, ROOT, check_json
from .indexed_memory import MemoryIndex
from .narration_codex import CodexNarrator
from .validation import validate
from .arcs import ArcRegistry, LONG_REVIEW_DELAY, decision_schema


SCHEMA_VERSION = '2.0'
SUMMARY_PERIOD = 20
EXCHANGE_BUDGET = 32000
SUMMARY_BUDGET = 16000
MARKER = ('\n'
          'WORLD REVIEW\n')
# Chronicler: rare rhythm, visible pressure, at most one big event at a time.
CHRONICLE_PERIOD = 7 * 1440
CHRONICLE_ELLIPSIS = 2 * 1440
CHRONICLE_HORIZON = 7 * 1440
MAX_CLOCKS = 8
MAX_NEW_CLOCKS = 1
MAX_STEP = 2
MAX_MANIFESTATIONS = 3
MAX_PENDING = 4
MAX_REPORTS = 2
REPORT_DELAY = 2 * 1440


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKD',text.casefold())
                   if not unicodedata.combining(c))


def long_wait(text):
    """Local trigger, not a mechanical estimate of the time to play."""
    text=normalized(text)
    if not re.search(r'\b(wait\w*|routine|work\w*|sleep\w*|slept|rest\w*|spend\w*|pass\w*|keep\w*|go on|going on|search\w*|read\w*|train\w*|watch\w*)\b',text):
        return False
    return bool(re.search(('\\b(week\\w*|month\\w*|year\\w*)\\b|\\b(\\d+|one|two|three|several|few)\\s+days?\\b|until.{0,45}\\b(evening|dawn|tomorrow|night)\\b|\\b(24|48|72)\\s*(h|hours)\\b'),text))


def long_activity(text):
    """Activity with no fixed end, to carry on until a concrete fact or an interruption."""
    text=normalized(text)
    return bool(re.search(('until (something|anything)\\b|until (the interruption|my return|the end|further '
                           'notice)|as long as|for as long as|without (stopping|interruption|a pause|a '
                           'break)|(for|during) (many|several|long) hours?\\b'),text))


def _obj(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}


def _txt(length):
    return {'type':'string','minLength':1,'maxLength':length}


def _refs(choices=None, maximum=32):
    item=_txt(150)
    if choices is not None:item['enum']=sorted(set(choices)) or ['no_source_available']
    return {'type':'array','minItems':1,'maxItems':maximum,'items':item}


def planner_schema(packet=None):
    packet=packet or {}
    period=[e['reference'] for e in packet.get('exchanges_to_summarize',[])] if packet else None
    blocks=[s['reference'] for s in packet.get('summaries_to_consolidate',[])] if packet else None
    allowed=packet.get('allowed_references') if packet else None
    def nullable(schema):
        return {**schema,'type':['object','null']}
    summary=_obj({'text':_txt(1800),'references':_refs(period)})
    synthesis=_obj({'text':_txt(1800),'references':_refs(blocks)})
    clock=_obj({
        'id':{'type':'string','pattern':'^[a-z0-9_.-]{1,80}$'},
        'expected_revision':{'type':'integer','minimum':-1},
        'faction':_txt(120),'goal':_txt(600),
        'segments':{'type':'integer','minimum':3,'maximum':10},
        'progression':{'type':'integer','minimum':0,'maximum':10},
        'status':{'type':'string','enum':['in_progress','burst','closed']},
        'signs':_txt(600),'event':_txt(1000),
        'origin':{'type':'string','enum':['canon','invention']},
        'reason':_txt(600),'references':_refs(allowed,8)})
    manifestation=_obj({
        'id':{'type':'string','pattern':'^[a-z0-9_.-]{1,80}$'},
        'clock':{'type':'string','pattern':'^[a-z0-9_.-]{1,80}$'},
        'type':{'type':'string','enum':['sign','event']},
        'title':_txt(160),'actor':_txt(120),'place':_txt(120),
        'description':_txt(600),'perception':_txt(300),
        'due_minute':{'type':'integer','minimum':0}})
    chronicle=_obj({'clocks':{'type':'array','maxItems':MAX_CLOCKS,'items':clock},
                    'manifestations':{'type':'array','maxItems':MAX_MANIFESTATIONS,'items':manifestation}})
    if packet.get('chronicle',{}).get('arcs_registry') is not None:
        chronicle['properties']['arcs']=decision_schema(allowed or [])
        chronicle['required'].append('arcs')
    if packet and 'chronicle' not in packet:chronicle={'type':'null'}
    elif packet:chronicle=chronicle
    else:chronicle=nullable(chronicle)
    return _obj({'planner_schema_version':{'type':'string','const':SCHEMA_VERSION},
                 'player_summary':nullable(summary),
                 'old_synthesis':nullable(synthesis),
                 'chronicle':chronicle})


class CodexPlanner(CodexNarrator):
    def schema_for_prompt(self,prompt):
        return planner_schema(json.loads(prompt.rsplit(MARKER,1)[1]))

    def validate_response(self,response,schema):
        validate(schema,response)


class WorldPlanner:
    def __init__(self,con):
        self.con=con

    def exists(self):
        return self.con.execute("SELECT 1 FROM sqlite_master WHERE name='planner_meta'").fetchone() is not None

    def chronicle_exists(self):
        return self.con.execute("SELECT 1 FROM sqlite_master WHERE name='chronicle_meta'").fetchone() is not None

    def initialize(self):
        for sql in (
            '''CREATE TABLE IF NOT EXISTS planner_meta (
                id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL,
                turn_count INTEGER NOT NULL,last_review_count INTEGER NOT NULL,
                last_summary_seq INTEGER NOT NULL,last_review_minute INTEGER NOT NULL,
                major_pending INTEGER NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS planner_turns (
                request_id TEXT PRIMARY KEY,substantial INTEGER NOT NULL,major INTEGER NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS planner_situations (
                id TEXT PRIMARY KEY,revision INTEGER NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS planner_history (
                id TEXT NOT NULL,revision INTEGER NOT NULL,review_id TEXT NOT NULL,payload TEXT NOT NULL,
                PRIMARY KEY(id,revision))''',
            '''CREATE TABLE IF NOT EXISTS planner_summaries (
                id INTEGER PRIMARY KEY,start_seq INTEGER NOT NULL,end_seq INTEGER NOT NULL,
                level INTEGER NOT NULL,audience TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS planner_reviews (
                id TEXT PRIMARY KEY,status TEXT NOT NULL,packet TEXT NOT NULL,
                response TEXT,metrics TEXT NOT NULL,reason TEXT NOT NULL)'''):
            self.con.execute(sql)
        self.con.execute('INSERT OR IGNORE INTO planner_meta VALUES (1,1,0,-1,0,0,0)')
        if self.con.execute('SELECT version FROM planner_meta').fetchone()[0]!=1:
            raise ValueError('Incompatible planner version.')

    def install_chronicle(self,clocks,chronology):
        """Additive tables of the chronicler and initial clocks, with no played event."""
        self.initialize()
        for sql in (
            '''CREATE TABLE IF NOT EXISTS chronicle_meta (
                id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL,
                last_review_minute INTEGER,chronology TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS chronicle_clocks (
                id TEXT PRIMARY KEY,revision INTEGER NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS chronicle_history (
                id TEXT NOT NULL,revision INTEGER NOT NULL,review_id TEXT NOT NULL,payload TEXT NOT NULL,
                PRIMARY KEY(id,revision))'''):
            self.con.execute(sql)
        self.con.execute('INSERT OR IGNORE INTO chronicle_meta VALUES (1,1,NULL,?)',(chronology,))
        nodes={'chronology:'+n['id'] for n in self.chronology_nodes()}
        added=0
        for item in clocks:
            if self.con.execute('SELECT 1 FROM chronicle_clocks WHERE id=?',(item['id'],)).fetchone():continue
            item={**item,'expected_revision':-1}
            validate(planner_schema()['properties']['chronicle']['properties']['clocks']['items'],item)
            self._check_clock(item,None,nodes|{r for r in item['references'] if not r.startswith('chronology:')},initial=True)
            if item['status']!='in_progress' or item['progression']>=item['segments']:
                raise ValueError('An initial clock stays in progress, with no burst event.')
            self._put_clock(item,'installation');added+=1
        if self.con.execute("SELECT COUNT(*) FROM chronicle_clocks WHERE status='in_progress'").fetchone()[0]>MAX_CLOCKS:
            raise ValueError('Too many active clocks.')
        return added

    def record_turn(self,proposal,result,before=None):
        if not self.exists() or result['status']!='recorded':return
        changes=[c for e in result.get('applied_events', proposal['events']) for c in e['changes']]
        substantial=result['world_minute_after']>(before or {}).get('world_minute',result['world_minute_after'])
        major=False
        for change in changes:
            group,key=change['path'].split('/')[1:]
            old=(before or {}).get(group,{}).get(key)
            value=change['value']
            differs=change['operation']!='set' or old!=value
            if differs and not (group=='intentions' and key=='player'):substantial=True
            if not differs:continue
            if group=='relations' and (old is None or (isinstance(value,dict) and
                    value.get('attitude')!=(old or {}).get('attitude'))):major=True
            if group=='commitments' and (old is None or (isinstance(value,dict) and
                    value.get('status')!=(old or {}).get('status'))):major=True
            if group=='powers' and old is None:major=True
            if group=='inventory' and re.search(r'rank|plaque',key):major=True
        cursor=self.con.execute('INSERT OR IGNORE INTO planner_turns VALUES (?,?,?)',
                                (proposal['request_id'],int(substantial),int(major)))
        if cursor.rowcount:
            self.con.execute('UPDATE planner_meta SET turn_count=turn_count+?,major_pending=MAX(major_pending,?)',
                             (int(substantial),int(major)))

    def situations(self):
        """Old planner leads, kept to close the projects already under way."""
        if not self.exists():return []
        return [{**json.loads(r['payload']),'revision':r['revision'],
                 'reference':f"plan:{r['id']}:{r['revision']}"}
                for r in self.con.execute("SELECT * FROM planner_situations WHERE status IN ('reserve','active') ORDER BY id")]

    def clocks(self,statuses=('in_progress','burst')):
        if not self.chronicle_exists():return []
        marks=','.join('?' for _ in statuses)
        return [{**json.loads(r['payload']),'revision':r['revision'],
                 'reference':f"chronicle:{r['id']}:{r['revision']}"}
                for r in self.con.execute('SELECT * FROM chronicle_clocks WHERE status IN ('+marks+') ORDER BY id',statuses)]

    def chronology_nodes(self):
        if not self.chronicle_exists():return []
        path=ROOT/self.con.execute('SELECT chronology FROM chronicle_meta').fetchone()[0]
        if not path.resolve().is_relative_to(ROOT.resolve()):raise ValueError('Chronology outside the project.')
        data=json.loads(path.read_text(encoding='utf-8'))
        # The scenes of volumes 6 and 8 often carry a plain time-of-day title.
        # Keeping proofs, markers and partial order avoids turning them into contentless titles.
        return [{'id':n['id'],'phase':n.get('phase'),'title':n['title'],
                 'time_marker':n.get('relative_time'),'date_source':n.get('calendar_date'),
                 'proofs':n.get('evidence',[]),
                 'after':[e['before'] for e in data.get('edges',[]) if e['after']==n['id']]}
                for n in data['nodes']]

    def summaries(self,recipient='narrator',limit=10):
        if not self.exists():return []
        rows=self.con.execute('''SELECT * FROM planner_summaries WHERE status='active'
            AND EXISTS (SELECT 1 FROM json_each(audience) WHERE value=?)
            ORDER BY end_seq DESC,id DESC LIMIT ?''',(recipient,limit))
        output=[];size=0
        for row in rows:
            item={**json.loads(row['payload']),'reference':'summary:'+str(row['id']),
                  'start_seq':row['start_seq'],'end_seq':row['end_seq'],'level':row['level'],
                  'audience':json.loads(row['audience'])}
            length=len(check_json(item))
            if size+length>SUMMARY_BUDGET:break
            output.append(item);size+=length
        return list(reversed(output))

    def narrator_packet(self,action='',position=''):
        if not self.exists():return {}
        packet={'player_summaries':self.summaries('player',6),
                'instruction':'Summaries are sourced aids, not new facts. State and archives take precedence.'}
        clocks=self.clocks()
        if clocks:
            keys=('id','faction','goal','progression','segments','status','signs')
            packet['world_in_motion']=[{k:c[k] for k in keys} for c in clocks]
            packet['world_instruction']=(('Secret clocks of the factions: they color the world through perceivable signs (rumors, '
                                       'prices, travelers, orders), without revealing their goal. The chronicle:* deadlines of '
                                       'the agenda are mandatory: manifest them to the player character on their date, or '
                                       'postpone them with a cause if the player character cannot materially perceive them.'))
        registry=ArcRegistry(self.con)
        if registry.exists():
            packet['private_world_facts']=registry.facts(query=action+' '+position)
            packet['arcs_instruction']=(("These off-screen facts are acquired by the world at their date, not known to the player "
                                      "character. A revelation requires a credible local means: report, witness, mail or "
                                      "observation. Do not re-execute the fact nor invent that the player character took part "
                                      "in it. Intentions do not prove their success. Respect the agenda's arcs_review_horizon "
                                      "for ellipses."))
        return packet

    def pending_exchanges(self):
        if self.con.execute("SELECT 1 FROM sqlite_master WHERE name='memory_sources'").fetchone() is None:return 0
        last=self.con.execute('SELECT last_summary_seq FROM planner_meta').fetchone()[0]
        return self.con.execute("SELECT COUNT(*) FROM memory_sources WHERE kind='exchange' AND seq>?",(last,)).fetchone()[0]

    def reasons(self,action):
        if self.con.execute("SELECT 1 FROM sqlite_master WHERE name='memory_sources'").fetchone():
            MemoryIndex(self.con).sync()
        reasons=[]
        if self.pending_exchanges()>=SUMMARY_PERIOD:reasons.append('periodic_summary')
        if self.chronicle_exists():
            minute=json.loads(self.con.execute('SELECT state FROM campaign').fetchone()[0])['world_minute']
            last=self.con.execute('SELECT last_review_minute FROM chronicle_meta').fetchone()[0]
            if last is None:reasons.append('first_chronicle')
            elif minute-last>=CHRONICLE_PERIOD:reasons.append('periodic_chronicle')
            elif long_wait(action) and minute-last>=CHRONICLE_ELLIPSIS:reasons.append('long_ellipsis')
            registry=ArcRegistry(self.con)
            if registry.exists() and registry.packet(minute)['to_process']:
                reasons.append('arcs_to_follow')
        return reasons

    def _summary(self,start,end,summary,audience,level=0):
        cursor=self.con.execute('INSERT INTO planner_summaries VALUES (NULL,?,?,?,?,?,?)',
            (start,end,level,check_json(audience),'active',check_json(summary)))
        return cursor.lastrowid

    def seed_archive(self):
        """Local extractive priming: identified quotations, never reconstructed facts."""
        meta=self.con.execute('SELECT * FROM planner_meta').fetchone()
        if meta['last_summary_seq']:return
        sources=self.con.execute("SELECT seq,text FROM memory_sources WHERE kind='exchange' ORDER BY seq").fetchall()
        for start in range(0,len(sources),10):
            block=sources[start:start+10]
            # Keep whole sentences and explicitly flag the selection.
            lines=[]
            for row in block:
                sentences=re.split(r'(?<=[.!?])\s+',row['text'])
                passage=' '.join([sentences[0],sentences[-1]]) if len(sentences)>1 else sentences[0]
                if len(passage)>260:passage=next((s for s in sentences if len(s)<=260),'Long text: consult the complete source.')
                lines.append(f"[exchange:{row['seq']}] {passage}")
            self._summary(block[0]['seq'],block[-1]['seq'],
                {'text':('Partial historical excerpts, consult the sources for details.\n')+'\n'.join(lines),
                 'references':['exchange:'+str(r['seq']) for r in block]},['narrator','player'])
        seq=self.con.execute("SELECT COALESCE(MAX(seq),0) FROM memory_sources WHERE kind='exchange'").fetchone()[0]
        self.con.execute('UPDATE planner_meta SET last_summary_seq=?',(seq,))

    def _put_clock(self,item,review_id):
        old=self.con.execute('SELECT revision FROM chronicle_clocks WHERE id=?',(item['id'],)).fetchone()
        revision=old[0]+1 if old else 0
        item={k:v for k,v in item.items() if k not in ('revision','reference')}
        self.con.execute('INSERT OR REPLACE INTO chronicle_clocks VALUES (?,?,?,?)',
                         (item['id'],revision,item['status'],check_json(item)))
        self.con.execute('INSERT INTO chronicle_history VALUES (?,?,?,?)',
                         (item['id'],revision,review_id,check_json(item)))
        return revision

    def packet(self,prepared,reasons):
        index=MemoryIndex(self.con);index.sync()
        meta=dict(self.con.execute('SELECT * FROM planner_meta').fetchone())
        state=prepared['state']
        exchanges=[];size=0
        for row in self.con.execute("SELECT * FROM memory_sources WHERE kind='exchange' AND seq>? ORDER BY seq LIMIT ?",
                                    (meta['last_summary_seq'],SUMMARY_PERIOD)):
            previous=self.con.execute('SELECT seq,role,content FROM exchanges WHERE seq=? AND role=\'player\'',(row['seq']-1,)).fetchone()
            item={'seq':row['seq'],'reference':row['reference'],'narration':row['text'],
                  'input':dict(previous) if previous else None}
            length=len(check_json(item))
            if exchanges and size+length>EXCHANGE_BUDGET:break
            if length>EXCHANGE_BUDGET:
                item['narration']=item['narration'][:EXCHANGE_BUDGET//2]
                item['partial_excerpt']=True
            exchanges.append(item);size+=len(check_json(item))
        consolidation=[{**json.loads(r['payload']),'reference':'summary:'+str(r['id']),
                        'start_seq':r['start_seq'],'end_seq':r['end_seq'],'level':r['level']}
                       for r in self.con.execute("SELECT * FROM planner_summaries WHERE status='active' AND audience=? ORDER BY end_seq,id LIMIT 6",(check_json(['narrator','player']),))]
        if len(consolidation)<6:consolidation=[]
        chronicle=bool({'first_chronicle','periodic_chronicle','long_ellipsis','arcs_to_follow'} & set(reasons))
        recent=self.summaries('player',4 if chronicle else 2)
        packet={'triggers':reasons,'meta':meta,
                'current_state':{'state_version':state['state_version'],'versions':state['versions'],
                                'world_minute':state['world_minute'],'date':date(state['world_minute']),
                                'positions':state.get('positions',{})},
                'action_to_resolve':prepared['context']['player_action'],
                'exchanges_to_summarize':exchanges,'summaries_to_consolidate':consolidation,'recent_summaries':recent}
        allowed={e['reference'] for e in exchanges}|{s['reference'] for s in consolidation}|{s['reference'] for s in recent}
        if chronicle:
            agenda=[{'entry_id':r['entry_id'],'revision':r['revision'],'due_minute':r['due_minute'],
                     'title':json.loads(r['payload'])['title'],'actor':json.loads(r['payload'])['actor']}
                    for r in self.con.execute("SELECT * FROM world_agenda WHERE status IN ('planned','postponed') ORDER BY due_minute,entry_id LIMIT 24")]
            resolved=[{'entry_id':r['entry_id'],'status':r['status'],'reason':json.loads(r['payload'])['reason']}
                      for r in self.con.execute("SELECT * FROM world_agenda WHERE entry_id LIKE 'chronicle:%' "
                                                "AND status NOT IN ('planned','postponed') ORDER BY due_minute DESC LIMIT 8")]
            clocks=self.clocks()
            nodes=self.chronology_nodes()
            packet['chronicle']={'clocks':clocks,'canon_chronology':nodes,'agenda_active':agenda,
                'manifestations_recentes':resolved,
                'limits':{'active_clocks':MAX_CLOCKS,'new_clocks':MAX_NEW_CLOCKS,'max_advance':MAX_STEP,
                           'manifestations_par_revision':MAX_MANIFESTATIONS,'pending_manifestations':MAX_PENDING,
                           'horizon_minutes':CHRONICLE_HORIZON}}
            allowed|={c['reference'] for c in clocks}|{'chronology:'+n['id'] for n in nodes}
            allowed|={'agenda:'+a['entry_id'] for a in agenda}
            registry=ArcRegistry(self.con)
            if registry.exists():
                rp=registry.packet(state['world_minute'])
                if long_activity(prepared['context']['player_action']):
                    # The delay only applies to this review; without a request from the player it stays at two days.
                    rp['max_review_delay']=LONG_REVIEW_DELAY
                    rp['delay_instruction']=(('The player asked for a long activity with no fixed end: set next_review to the '
                                           'really useful date, up to max_review_delay, rather than a few hours away. A concrete '
                                           'fact stays interrupted by the agenda, not by the review.'))
                packet['chronicle']['arcs_registry']=rp
                packet['chronicle']['private_world_facts']=registry.facts()
                # Precise local proofs for obstacles and divergences, with no remote search.
                evidence=[{'reference':r['event_id'],**json.loads(r['payload'])}
                          for r in self.con.execute('SELECT event_id,payload FROM events ORDER BY rowid DESC LIMIT 12')]
                packet['chronicle']['campaign_proofs']=evidence
                allowed|={a['reference'] for a in rp['arcs']}
                allowed|={f['reference'] for f in packet['chronicle']['private_world_facts']}
                allowed|={e['reference'] for e in evidence}
                # The written milestones of the registry are passed with their anchors, not all the novels.
                allowed|={'chronology:'+n for a in rp['arcs'] for n in a['nodes']}
        if chronicle:
            orphans=sorted(self._reopenable({c['id']:c for c in clocks},{c['id']:c for c in clocks},packet))
            if orphans:
                packet['chronicle']['burst_clocks_without_deadline']=orphans
                packet['chronicle']['resume_instruction']=(('These clocks have burst but their big event no longer has an active deadline: if its '
                                                          'outcome is not established, a manifestation type=event dated within the horizon '
                                                          'restores it without modifying the clock; otherwise close the clock or realize the arc. '
                                                          'For the linked arc, motif=obstacle with the wait for this event as cause: no proof '
                                                          'required, postponement not counted.'))
        packet['allowed_references']=sorted(allowed)
        return packet

    def _check_clock(self,item,old,allowed,initial=False):
        if any(ref not in allowed for ref in item['references']):
            raise ValueError('Clock without an authorized reference: '+item['id'])
        if item['origin']=='canon' and not any(r.startswith('chronology:') for r in item['references']):
            raise ValueError('A clock drawn from the canon requires a provided chronology:* reference.')
        if item['progression']>item['segments']:
            raise ValueError('Progress greater than the number of segments: '+item['id'])
        start=old['progression'] if old else 0
        if item['progression']-start>MAX_STEP and not initial:
            raise ValueError('Advance too fast for the clock '+item['id']+' : '+str(MAX_STEP)+' segments at most.')
        if item['status']=='burst' and item['progression']!=item['segments']:
            raise ValueError('An event only bursts with a full clock: '+item['id'])
        if item['status']=='in_progress' and item['progression']>=item['segments']:
            raise ValueError('A full clock must burst or be closed: '+item['id'])
        if old:
            if old['status']=='closed':raise ValueError('Clock closed: '+item['id'])
            if old['status']=='burst' and item['status']=='in_progress':
                raise ValueError('A burst clock does not return to in progress: '+item['id'])
            if item['segments']!=old['segments']:raise ValueError('The number of segments of a clock is fixed.')

    def _reopenable(self,known,final,packet):
        """Burst clocks whose big event no longer has an active deadline: resume allowed.

        Case of an event closed as a mere sign without its outcome being established.
        An already terminal linked arc, a closed clock or a still active deadline forbid it.
"""
        arcs={a['clock']:a for a in packet['chronicle'].get('arcs_registry',{}).get('arcs',[]) if a.get('clock')}
        active=set()
        # The first revision of a deadline keeps the original clock, even if its references are replaced.
        for r in self.con.execute("SELECT h.payload FROM world_agenda w JOIN agenda_history h "
                                  "ON h.entry_id=w.entry_id AND h.revision=0 "
                                  "WHERE w.entry_id LIKE 'chronicle:%' AND w.status IN ('planned','postponed')"):
            entry=json.loads(r[0])
            if entry.get('interrupt'):
                active.update(ref.split(':')[1] for ref in entry.get('references',[]) if ref.startswith('chronicle:') and ref.count(':')==2)
        return {i for i,c in known.items()
                if c['status']=='burst' and final.get(i,{}).get('status')=='burst' and i not in active
                and (i not in arcs or arcs[i]['status'] not in ('achieved','prevented','diverted'))}

    def _apply_chronicle(self,review_id,packet,chronicle,state):
        allowed=set(packet['allowed_references'])
        agenda=Agenda(self.con)
        for entry in packet['chronicle']['agenda_active']:
            current=agenda.get(entry['entry_id'])
            if current is None or current['revision']!=entry['revision']:
                raise ValueError('Agenda modified during planning.')
        known={c['id']:c for c in packet['chronicle']['clocks']}
        ids=[c['id'] for c in chronicle['clocks']]
        if len(ids)!=len(set(ids)):raise ValueError('Clock modified twice.')
        final={c['id']:c for c in self.clocks(('in_progress','burst','closed'))}
        new=0;burst=set()
        for item in chronicle['clocks']:
            existing=self.con.execute('SELECT revision FROM chronicle_clocks WHERE id=?',(item['id'],)).fetchone()
            if item['expected_revision']!=(existing[0] if existing else -1) or (existing and item['id'] not in known):
                raise ValueError('Obsolete clock revision or archived clock: '+item['id'])
            old=known.get(item['id'])
            # A cited revision already carries its verified canonical anchor.
            # Keep it explicitly, without inferring a source for a creation.
            if (old and old['origin']==item['origin']=='canon'
                    and old['reference'] in item['references']
                    and not any(r.startswith('chronology:') for r in item['references'])):
                anchors=[r for r in old['references'] if r.startswith('chronology:') and r in allowed]
                if anchors and len(item['references'])<8:
                    item={**item,'references':[*item['references'],anchors[0]]}
            self._check_clock(item,old,allowed)
            if not old:new+=1
            if item['status']=='burst' and (not old or old['status']!='burst'):burst.add(item['id'])
            final[item['id']]=item
        if new>MAX_NEW_CLOCKS:raise ValueError('A single new clock per revision.')
        if sum(c['status']=='in_progress' for c in final.values())>MAX_CLOCKS:
            raise ValueError('Too many active clocks; close or burst one before adding another.')
        manifestations=chronicle['manifestations']
        if len({m['id'] for m in manifestations})!=len(manifestations):raise ValueError('Manifestation en double.')
        if sum(m['type']=='event' for m in manifestations)>1:raise ValueError('A single big event per revision.')
        pending=self.con.execute("SELECT COUNT(*) FROM world_agenda WHERE entry_id LIKE 'chronicle:%' AND status IN ('planned','postponed')").fetchone()[0]
        if pending+len(manifestations)>MAX_PENDING:
            raise ValueError('Too many chronicle manifestations pending.')
        due_counts={r[0]:r[1] for r in self.con.execute(
            "SELECT due_minute,COUNT(*) FROM world_agenda WHERE status IN ('planned','postponed') GROUP BY due_minute")}
        reopen=self._reopenable(known,final,packet)
        for m in manifestations:
            clock=final.get(m['clock'])
            if clock is None:raise ValueError('Manifestation without a known clock: '+m['id'])
            if m['type']=='event' and m['clock'] not in burst and m['clock'] not in reopen:
                raise ValueError('A big event requires its clock to burst in this revision.')
            if m['type']=='sign' and clock['status']!='in_progress':
                raise ValueError('A sign announces a clock that is still in progress: '+m['id'])
            if not state['world_minute']<m['due_minute']<=state['world_minute']+CHRONICLE_HORIZON:
                raise ValueError('A manifestation requires a strictly future date, seven days at most.')
            if agenda.get('chronicle:'+m['id']):raise ValueError('Manifestation identifier already used.')
            due_counts[m['due_minute']]=due_counts.get(m['due_minute'],0)+1
            if due_counts[m['due_minute']]>RESPONSE_LIMIT:raise ValueError('Agenda too busy at this date.')
        for item in burst:
            if not any(m['clock']==item and m['type']=='event' for m in manifestations):
                raise ValueError('A clock that bursts must produce its big event: '+item)
        if 'arcs_registry' in packet['chronicle']:
            registered={a['id']:a for a in packet['chronicle']['arcs_registry']['arcs']}
            for arc in registered.values():
                if arc.get('clock') in burst and any(registered[d]['status'] not in ('achieved','prevented','diverted')
                                                      for d in arc['dependencies']):
                    raise ValueError("Big event before the arc's dependencies: "+arc['id'])
            ArcRegistry(self.con).apply(review_id,
                {**packet['chronicle'],'allowed_references':packet['allowed_references']},
                chronicle['arcs'],state['world_minute'],final,
                {m['clock'] for m in manifestations if m['type']=='event'})
        revisions={}
        for item in chronicle['clocks']:
            revisions[item['id']]=self._put_clock(final[item['id']],review_id)
        for m in manifestations:
            revision=revisions.get(m['clock'],final[m['clock']].get('revision',0))
            label='Big event: ' if m['type']=='event' else 'Sign: '
            entry={'entry_id':'chronicle:'+m['id'],'expected_revision':-1,'title':m['title'],
                   'actor':m['actor'],'location':m['place'],'kind':'risk','due_minute':m['due_minute'],
                   'conditions':[],'references':[f"chronicle:{m['clock']}:{revision}"],'audience':['narrator'],
                   'interrupt':m['type']=='event','status':'planned',
                   'reason':label+m['description']+' Perception: '+m['perception'],
                   'outcome_event_index':None}
            agenda._put(entry,'planner:'+review_id,state['world_minute'])
        self.con.execute('UPDATE chronicle_meta SET last_review_minute=?',(state['world_minute'],))

    def apply_review(self,review_id,packet,response):
        validate(planner_schema(packet),response)
        check_json(response)
        state=json.loads(self.con.execute('SELECT state FROM campaign').fetchone()[0])
        meta=dict(self.con.execute('SELECT * FROM planner_meta').fetchone())
        if (state['state_version']!=packet['current_state']['state_version'] or meta!=packet['meta'] or
                state['versions']!=packet['current_state']['versions']):
            raise ValueError('World review obsolete; state or counter modified.')
        exchanges=packet['exchanges_to_summarize']
        summary=response['player_summary']
        if bool(exchanges)!=bool(summary):raise ValueError('A summary is required only for the new exchanges that were played.')
        if summary:
            source_refs={e['reference'] for e in exchanges}
            actual=set(summary['references'])
            if actual!=source_refs:
                raise ValueError('The current summary must cite exactly its period. '
                                 f"Period: {exchanges[0]['seq']} to {exchanges[-1]['seq']}; "
                                 f"missing references: {len(source_refs-actual)}; outside the period: {len(actual-source_refs)}.")
            self._summary(exchanges[0]['seq'],exchanges[-1]['seq'],summary,['narrator','player'])
        synthesis=response['old_synthesis'];blocks=packet['summaries_to_consolidate']
        if bool(blocks)!=bool(synthesis):raise ValueError('An old synthesis is required when six periods are provided.')
        if synthesis:
            if set(synthesis['references'])!={s['reference'] for s in blocks}:
                raise ValueError('The consolidation must reference all its periods, and only them.')
            self._summary(blocks[0]['start_seq'],blocks[-1]['end_seq'],synthesis,
                          ['narrator','player'],max(s['level'] for s in blocks)+1)
            for block in blocks:
                self.con.execute("UPDATE planner_summaries SET status='archive' WHERE id=?",(int(block['reference'].split(':')[1]),))
        if 'chronicle' in packet:
            if response['chronicle'] is None:raise ValueError('Chronicle required for this review.')
            self._apply_chronicle(review_id,packet,response['chronicle'],state)
        self.con.execute('UPDATE planner_meta SET last_review_count=turn_count,last_summary_seq=?,last_review_minute=?,major_pending=0',
                         (exchanges[-1]['seq'] if exchanges else meta['last_summary_seq'],state['world_minute']))
        self.con.execute("UPDATE planner_reviews SET status='applied' WHERE id=?",(review_id,))

    def sync_projects(self):
        """Archive only the projects whose deadline the narrator really closed."""
        if not self.exists():return
        for item in self.situations():
            if item['status']!='active':continue
            entry=Agenda(self.con).get('world:'+item['id'])
            if entry and entry['status']=='done':
                # Follow the chain of steps, whose links can be events.
                frontier=[entry];leaves=[];seen=set()
                while frontier and len(seen)<64:
                    parent=frontier.pop()
                    if parent['entry_id'] in seen:continue
                    seen.add(parent['entry_id'])
                    refs=['agenda:'+parent['entry_id']]
                    history=self.con.execute('SELECT request_id FROM agenda_history WHERE entry_id=? ORDER BY revision DESC LIMIT 1',
                                             (parent['entry_id'],)).fetchone()
                    if history and parent['outcome_event_index'] is not None:
                        result=self.con.execute('SELECT result FROM requests WHERE request_id=?',(history[0],)).fetchone()
                        events=json.loads(result[0]).get('applied_events',[]) if result else []
                        index=parent['outcome_event_index']
                        if index<len(events):refs.append(events[index]['event_id'])
                    marks=','.join('?' for _ in refs)
                    children=[{**json.loads(r['payload']),'revision':r['revision']} for r in self.con.execute(
                        "SELECT * FROM world_agenda WHERE entry_id!=? AND EXISTS "
                        "(SELECT 1 FROM json_each(json_extract(payload,'$.references')) WHERE value IN ("+marks+"))",
                        (parent['entry_id'],*refs)) if json.loads(r['payload'])['actor']==entry['actor']]
                    if children:frontier.extend(children)
                    else:leaves.append(parent)
                if leaves and not frontier and all(e['status'] in ('finished','cancelled') for e in leaves):
                    entry=leaves[-1]
            if entry and entry['status'] in ('finished','cancelled'):
                # An accomplished step is not a closing of the whole project.
                item={k:v for k,v in item.items() if k not in ('revision','reference')}
                item['status']='finished' if entry['status']=='finished' else 'discarded'
                item['reason']='Closing recorded by the narrator: '+entry['reason']
                old=self.con.execute('SELECT revision FROM planner_situations WHERE id=?',(item['id'],)).fetchone()
                revision=old[0]+1 if old else 0
                self.con.execute('INSERT OR REPLACE INTO planner_situations VALUES (?,?,?,?)',
                                 (item['id'],revision,item['status'],check_json(item)))
                self.con.execute('INSERT INTO planner_history VALUES (?,?,?,?)',
                                 (item['id'],revision,'agenda:'+entry['entry_id'],check_json(item)))


def instructions_for(packet):
    text=(ASSETS/'docs/engine/agents/planner.md').read_text(encoding='utf-8')
    if 'chronicle' in packet:text+='\n'+(ASSETS/'docs/engine/agents/chronicler.md').read_text(encoding='utf-8')
    language=str(packet.get('narration_language','English'))
    return text+'\nNarration language: '+language+'.'+'\n'


def review_world(campaign,adapter,prepared,retry=False):
    """At most one distinct call, persisted before generation; resume without a loop."""
    pending=prepared['resume']['session']['pending_action']
    root=(pending.get('previous_request_ids') or [pending['request_id']])[0]
    key=hashlib.sha256((root+':'+str(prepared['state']['state_version'])+':'+check_json(prepared['state']['versions'])).encode()).hexdigest()
    with campaign.transaction() as con:
        planner=WorldPlanner(con)
        if not planner.exists():return {'model_calls':0,'status':'disabled'}
        old=con.execute('SELECT * FROM planner_reviews WHERE id=?',(key,)).fetchone()
        if old and old['status']=='applied':return {'model_calls':0,'status':'reused'}
        interrupted=con.execute("SELECT id FROM planner_reviews WHERE status IN ('in_progress','failure','prepare') "
                                "AND json_extract(packet,'$.request_root')=? AND id!=? LIMIT 1",(root,key)).fetchone()
        if interrupted and not retry:
            raise ModelUnavailable('An old preparation of this action remains interrupted; use --resume --retry.')
        if interrupted and retry:
            con.execute("UPDATE planner_reviews SET status='replaced' WHERE id=?",(interrupted[0],))
        reusable=old and old['response'] and not retry
        if old and old['status'] in ('in_progress','failure') and not reusable and not retry:
            raise ModelUnavailable('Planning interrupted or invalid; use --resume --retry. No automatic call.')
        reasons=planner.reasons(prepared['context']['player_action'])
        if not old and not reasons:return {'model_calls':0,'status':'not_required'}
        packet=json.loads(old['packet']) if reusable else planner.packet(prepared,reasons)
        packet['request_root']=root
        packet.setdefault('narration_language',prepared['campaign_rules'].get('narration_language','English'))
        response=json.loads(old['response']) if reusable else None
        if response is None:
            if old:
                con.execute('''CREATE TABLE IF NOT EXISTS planner_review_history (
                    seq INTEGER PRIMARY KEY,id TEXT,status TEXT,packet TEXT,response TEXT,metrics TEXT,reason TEXT)''')
                con.execute('INSERT INTO planner_review_history(id,status,packet,response,metrics,reason) VALUES (?,?,?,?,?,?)',tuple(old))
            con.execute('INSERT OR REPLACE INTO planner_reviews VALUES (?,?,?,?,?,?)',
                        (key,'in_progress',check_json(packet),None,'{}',''))
    metrics={'model_calls':0,'status':'reused','triggers':packet['triggers']}
    launched=False
    try:
        if response is None:
            instructions=instructions_for(packet)
            docs=[]
            prompt=instructions+'\nDOCUMENTS\n'+check_json(docs)+MARKER+check_json(packet)
            from .narrative import CONTEXT_BUDGET_BYTES,prompt_metrics
            metrics['context_blocks']=prompt_metrics(instructions,docs,packet,'\nDOCUMENTS\n'+MARKER)
            if len(prompt.encode('utf-8'))>CONTEXT_BUDGET_BYTES:
                raise ModelUnavailable('Planner core beyond the UTF-8 budget; zero generation.')
            with campaign.transaction() as con:
                con.execute('''CREATE TABLE IF NOT EXISTS planner_prompts (
                    seq INTEGER PRIMARY KEY,review_id TEXT,prompt TEXT)''')
                con.execute('INSERT INTO planner_prompts(review_id,prompt) VALUES (?,?)',(key,prompt))
            metrics.update(model_calls=1,status='revision',prompt_bytes=len(prompt.encode('utf-8')))
            started=time.monotonic()
            def receive(raw):
                with campaign.transaction() as con:
                    con.execute('''CREATE TABLE IF NOT EXISTS planner_raw_receipts (
                        seq INTEGER PRIMARY KEY,review_id TEXT,prompt TEXT,raw TEXT)''')
                    con.execute('INSERT INTO planner_raw_receipts(review_id,prompt,raw) VALUES (?,?,?)',
                                (key,prompt,json.dumps(raw,ensure_ascii=True)))
                    con.execute("UPDATE planner_reviews SET response=? WHERE id=?",
                                (json.dumps(raw,ensure_ascii=True),key))
            adapter.receipt_callback=receive
            launched=True
            try:
                response=adapter.generate(prompt)
            finally:
                adapter.receipt_callback=None
            metrics.update(getattr(adapter,'last_metrics',{}))
            metrics['generation_seconds']=round(time.monotonic()-started,3)
            with campaign.transaction() as con:
                # Keep invalid outputs as escaped ASCII for the diagnostics.
                con.execute("UPDATE planner_reviews SET status='prepare',response=?,metrics=? WHERE id=?",
                    (json.dumps(response,ensure_ascii=True,allow_nan=False),check_json(metrics),key))
        if isinstance(response,str):response=json.loads(response)
        with campaign.transaction() as con:
            WorldPlanner(con).apply_review(key,packet,response)
        return metrics
    except (ModelUnavailable,ValueError,TypeError,KeyError,KeyboardInterrupt) as exc:
        if launched:metrics.update(getattr(adapter,'last_metrics',{}))
        reason=str(exc).replace(chr(0x2014),'-')
        with campaign.transaction() as con:
            raw=getattr(adapter,'last_response',None) if launched else None
            if launched and raw is None and getattr(adapter,'last_error',None) is not None:
                raw={'cli_error':adapter.last_error}
            status='failure' if launched or reusable else 'preparation_refused'
            con.execute("UPDATE planner_reviews SET status=?,reason=?,response=COALESCE(response,?),metrics=? WHERE id=?",
                        (status,reason,json.dumps(raw,ensure_ascii=True) if raw else None,check_json(metrics),key))
        if isinstance(exc,KeyboardInterrupt):raise
        if isinstance(exc,AwaitingAnswer):
            raise AwaitingAnswer('Waiting for a manual answer to the world review; input kept. '+reason) from exc
        raise ModelUnavailable('Planning interrupted or invalid; input kept. '+reason) from exc
