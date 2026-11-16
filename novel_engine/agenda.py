"""Transactional agenda of the world, on the fictional clock only."""
from copy import deepcopy
import hashlib
import json
import re
import secrets

from .campaign import conditions_hold, pointer, GROUPS
from .contracts import check_json
from .validation import validate

ACTIVE = ('planned', 'postponed')
PACKET_LIMIT = 16
PACKET_CHARS = 16000
RESPONSE_LIMIT = 32
CHRONICLE_PREFIX = 'chronicle:'
QUIET_NEWS_PREFIX = 'pacing:quiet_news:'
CHRONICLE_REPORTS = 2
CHRONICLE_REPORT_DELAY = 2 * 1440
# A scene may run this many minutes past the arcs review before it has to be cut (3 hours).
ARC_HORIZON_TOLERANCE = 180


def manifests(entry):
    """An interruption, a manifestation of the chronicle or quiet news must be perceived by the player."""
    return bool(entry['interrupt'] or entry['entry_id'].startswith((CHRONICLE_PREFIX, QUIET_NEWS_PREFIX)))


def opening_minute(config):
    """Local minute of the day at which the campaign opens, when its pack gives one."""
    value = (config.get('reader') or {}).get('opening_minute_of_day')
    return value if type(value) is int and 0 <= value < 1440 else None


def date(minute, opening=None):
    if opening is not None:
        # A real local time, so that an elapsed offset can never be read as an hour of the day.
        day, local = divmod(opening + minute, 1440)
        return f'Day {day + 1}, {local // 60:02d}:{local % 60:02d}'
    # T0 matches the opening, without importing a canonical civil date.
    day, clock = divmod(minute, 1440)
    hour, minute = divmod(clock, 60)
    return f'D{day}, T0 + {hour:02d} h {minute:02d}'


def agenda_schema(condition_schema):
    identifier = {'type':'string','pattern':'^[a-z0-9_:.-]{1,100}$'}
    text = {'type':'string','minLength':1,'maxLength':1000}
    update = {'type':'object','additionalProperties':False,'properties':{
        'entry_id':identifier, 'expected_revision':{'type':'integer','minimum':-1},
        'title':{'type':'string','minLength':1,'maxLength':200},
        'actor':{'type':'string','minLength':1,'maxLength':120},
        'location':{'type':'string','minLength':1,'maxLength':120},
        'kind':{'type':'string','enum':['appointment','project','verification','risk']},
        'due_minute':{'type':'integer','minimum':0},
        'conditions':{'type':'array','maxItems':12,'items':deepcopy(condition_schema)},
        'references':{'type':'array','minItems':1,'maxItems':12,'items':{'type':'string','minLength':1}},
        'audience':{'type':'array','minItems':1,'maxItems':12,'items':{'type':'string','minLength':1}},
        'interrupt':{'type':'boolean'},
        'status':{'type':'string','enum':['planned','done','finished','postponed','cancelled']},
        'reason':text,
        'outcome_event_index':{'type':['integer','null'],'minimum':0},
    }}
    update['required']=list(update['properties'])
    continuation={'type':['object','null'],'additionalProperties':False,'properties':{
        'goal':text,'remaining':text,
        'status':{'type':'string','enum':['suspended','finished','abandoned']},
        'event_index':{'type':['integer','null'],'minimum':0}},
        'required':['goal','remaining','status','event_index']}
    rolls={'type':'array','maxItems':5,'items':{'type':'object','additionalProperties':False,'properties':{
        'key':{'type':'string','enum':['general','negotiation','works','combat','encounter']},
        'threshold':{'type':'integer','minimum':1,'maximum':100},
        'success':{'type':'boolean'},'reason':text},
        'required':['key','threshold','success','reason']}}
    return {'agenda_updates':{'type':'array','maxItems':RESPONSE_LIMIT,'items':update},
            'continuation':continuation,'roll_checks':rolls}


class Agenda:
    def __init__(self, con):
        self.con=con

    def exists(self):
        return self.con.execute("SELECT 1 FROM sqlite_master WHERE name='world_agenda'").fetchone() is not None

    def initialize(self):
        for sql in (
            '''CREATE TABLE IF NOT EXISTS agenda_meta (
                id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL,seed TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS world_agenda (
                entry_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,due_minute INTEGER NOT NULL,
                status TEXT NOT NULL,payload TEXT NOT NULL)''',
            '''CREATE INDEX IF NOT EXISTS agenda_active_due ON world_agenda(due_minute,entry_id)
                WHERE status IN ('planned','postponed')''',
            '''CREATE TABLE IF NOT EXISTS agenda_history (
                entry_id TEXT NOT NULL,revision INTEGER NOT NULL,request_id TEXT NOT NULL,
                world_minute INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(entry_id,revision))''',
            '''CREATE TABLE IF NOT EXISTS agenda_receipts (
                request_id TEXT PRIMARY KEY,payload TEXT NOT NULL)''',
            '''CREATE TABLE IF NOT EXISTS agenda_continuations (
                request_id TEXT PRIMARY KEY,world_minute INTEGER NOT NULL,payload TEXT NOT NULL)''',
        ):
            self.con.execute(sql)
        self.con.execute('INSERT OR IGNORE INTO agenda_meta VALUES (1,1,?)',(secrets.token_hex(32),))
        if self.con.execute('SELECT version FROM agenda_meta WHERE id=1').fetchone()[0]!=1:
            raise ValueError('Incompatible agenda version.')

    def _put(self, entry, request_id, minute, revision=0):
        self.con.execute('INSERT OR REPLACE INTO world_agenda VALUES (?,?,?,?,?)',
            (entry['entry_id'],revision,entry['due_minute'],entry['status'],check_json(entry)))
        self.con.execute('INSERT INTO agenda_history VALUES (?,?,?,?,?)',
            (entry['entry_id'],revision,request_id,minute,check_json(entry)))

    def sync_commitments(self, state):
        """Local safety net: a newly open agreement receives a check date."""
        self.initialize()
        self.sync_tracked_npcs(state)
        for key, commitment in state['commitments'].items():
            if commitment['status']!='open':
                continue
            identifier='accord:'+key
            if self.con.execute('SELECT 1 FROM world_agenda WHERE entry_id=?',(identifier,)).fetchone():
                continue
            entry={'entry_id':identifier,'expected_revision':-1,
                'title':('Check: '+key)[:200],'actor':'parties to the agreement',
                'location':commitment.get('reminder_location', 'place of the agreement'),'kind':'verification',
                'due_minute':state['world_minute']+1440,'conditions':[],
                'references':commitment['references'][:12],
                'audience':list(dict.fromkeys(['narrator',*commitment['audience']]))[:12],
                'interrupt':False,'status':'planned',
                'reason':('Follow-up of the recorded agreement, not a guarantee of fulfillment. '+commitment['description'])[:1000],
                'outcome_event_index':None}
            self._put(entry,'agenda:followup_agreement',state['world_minute'])

    def sync_tracked_npcs(self, state):
        """One off-screen step per NPC listed in the campaign configuration (`tracked_npcs`), created once."""
        row = self.con.execute('SELECT config FROM campaign WHERE id=1').fetchone()
        for npc in (json.loads(row[0]).get('tracked_npcs') or []) if row else []:
            identifier = 'npc_step:' + npc['id']
            if self.con.execute('SELECT 1 FROM world_agenda WHERE entry_id=?', (identifier,)).fetchone():
                continue
            goal = npc.get('goal') or 'pursue its own goals'
            entry = {'entry_id': identifier, 'expected_revision': -1,
                     'title': ('Next step of ' + npc['name'])[:200], 'actor': npc['name'],
                     'location': npc.get('place') or 'current place of ' + npc['name'], 'kind': 'project',
                     'due_minute': state['world_minute'] + 1440, 'conditions': [],
                     'references': ['npc:' + npc['id']], 'audience': ['narrator', 'npc:' + npc['id']],
                     'interrupt': False, 'status': 'planned',
                     'reason': ('Prepare the next off-screen step with the sources and knowledge available to '
                                + npc['name'] + '; the player receives no secret. Goal: ' + goal)[:1000],
                     'outcome_event_index': None}
            self._put(entry, 'agenda:tracked_npc', state['world_minute'])

    def opening(self):
        row=self.con.execute('SELECT config FROM campaign WHERE id=1').fetchone()
        return opening_minute(json.loads(row[0])) if row else None

    def turns_without_time(self, minute):
        """Latest recorded turns that left the clock on this minute, not counting the turn that reached it."""
        turns=0
        for (raw,) in self.con.execute('SELECT result FROM requests ORDER BY rowid DESC LIMIT 12'):
            result=json.loads(raw)
            if result.get('status')!='recorded':continue
            if result.get('world_minute_after')!=minute:
                break
            turns+=1
        return max(0,turns-1)

    def get(self, identifier):
        row=self.con.execute('SELECT * FROM world_agenda WHERE entry_id=?',(identifier,)).fetchone()
        if not row:
            return None
        return {**json.loads(row['payload']),'revision':row['revision']}

    def entries(self, recipient='narrator', limit=PACKET_LIMIT, required_through=None):
        rows=self.con.execute('''SELECT * FROM world_agenda
            WHERE status IN ('planned','postponed') AND EXISTS
            (SELECT 1 FROM json_each(json_extract(payload,'$.audience')) WHERE value=?)
            ORDER BY due_minute,entry_id''',(recipient,))
        output=[];size=0;groups={}
        opening=self.opening()
        for row in rows:
            item={**json.loads(row['payload']),'revision':row['revision'],
                  'date':date(row['due_minute'],opening),'reference':'agenda:'+row['entry_id']}
            groups.setdefault(row['due_minute'],[]).append(item)
        self.selection_diagnostic=None
        for minute,group in groups.items():
            length=sum(len(check_json(item)) for item in group)
            if len(output)+len(group)>RESPONSE_LIMIT or size+length>PACKET_CHARS:
                if not output:
                    self.selection_diagnostic={'due_minute':minute,'count':len(group),
                        'characters':length,'answer_limit':RESPONSE_LIMIT,'character_budget':PACKET_CHARS}
                break
            if output and len(output)+len(group)>limit and (required_through is None or minute>required_through):break
            output.extend(group);size+=length
        return output

    def rolls(self, request_id):
        seed=self.con.execute('SELECT seed FROM agenda_meta WHERE id=1').fetchone()[0]
        return {key:int.from_bytes(hashlib.sha256((seed+':'+request_id+':'+key).encode()).digest()[:8],'big')%100+1
                for key in ('general','negotiation','works','combat','encounter')}

    def chronicle_constraints(self, entries):
        """Private counters from the recorded postponements, without changing the deadlines."""
        identifiers = [e['entry_id'] for e in entries if e['entry_id'].startswith(CHRONICLE_PREFIX)]
        if not identifiers:
            return {}
        marks = ','.join('?' for _ in identifiers)
        counts = dict(self.con.execute(f'''SELECT entry_id,COUNT(*) FROM agenda_history
            WHERE entry_id IN ({marks}) AND json_extract(payload,'$.status')='postponed'
            GROUP BY entry_id''', identifiers))
        return {entry['entry_id']: {
            'postponements_done': counts.get(entry['entry_id'], 0),
            'postponements_left': max(0, CHRONICLE_REPORTS-counts.get(entry['entry_id'], 0)),
            'postponement_allowed': counts.get(entry['entry_id'], 0) < CHRONICLE_REPORTS,
            'postpone_by_minute': entry['due_minute']+CHRONICLE_REPORT_DELAY
                if counts.get(entry['entry_id'], 0) < CHRONICLE_REPORTS else None,
        } for entry in entries if entry['entry_id'] in identifiers}

    def packet(self, state, pending=None, recipient='narrator'):
        self.initialize()
        entries=self.entries(recipient,required_through=state['world_minute'])
        omitted = self.con.execute('''SELECT due_minute FROM world_agenda
            WHERE status IN ('planned','postponed') AND EXISTS
            (SELECT 1 FROM json_each(json_extract(payload,'$.audience')) WHERE value=?)
            ORDER BY due_minute,entry_id LIMIT 1 OFFSET ?''', (recipient, len(entries))).fetchone()
        if omitted and omitted[0]<=state['world_minute']:
            self.selection_diagnostic=self.selection_diagnostic or {'due_minute':omitted[0],
                'cause':'Deadlines already due were omitted; mandatory group exceeding the remaining capacity.',
                'answer_limit':RESPONSE_LIMIT,'character_budget':PACKET_CHARS}
        if recipient!='narrator':
            # Backstage conditions are not knowledge of the player.
            visible={'entry_id','title','actor','location','kind','due_minute','status',
                     'revision','date','reference','references'}
            entries=[{key:value for key,value in item.items() if key in visible} for item in entries]
        last=self.con.execute('SELECT * FROM agenda_continuations ORDER BY rowid DESC LIMIT 1').fetchone()
        continuation=json.loads(last['payload']) if last else None
        if continuation and continuation['status']!='suspended':continuation=None
        roots=(pending or {}).get('previous_request_ids',[])
        root=roots[0] if roots else (pending or {}).get('request_id')
        from .arcs import ArcRegistry
        arc_horizon=ArcRegistry(self.con).horizon(state['world_minute']) if recipient=='narrator' else None
        opening=self.opening()
        clock={'world_minute':state['world_minute'],'date':date(state['world_minute'],opening),
               'origin':('T0: common opening, without a canonical civil date' if opening is None else
                         'date and the deadline dates are real local times; world_minute counts the minutes elapsed since the opening')}
        if recipient=='narrator':
            clock['turns_without_time']=self.turns_without_time(state['world_minute'])
        from .scene_pacing import quiet_guidance
        guidance=quiet_guidance(self.con) if recipient=='narrator' else None
        return {'clock':clock,
                'events':entries,'context_limit':PACKET_LIMIT,'character_budget':PACKET_CHARS,
                'chronicle_constraints':self.chronicle_constraints(entries) if recipient=='narrator' else {},
                'max_horizon_if_limited':max(state['world_minute'],omitted[0]-1) if omitted else None,
                'arcs_review_horizon':arc_horizon,
                'technical_diagnostic':self.selection_diagnostic,
                'interrupted_goal':continuation if recipient=='narrator' else None,
                'd100_rolls':self.rolls(root) if root and recipient=='narrator' else {},
                **({'pacing_guidance':guidance} if guidance else {})}

    def _evolve(self, state, event):
        if not conditions_hold(state,event['conditions']):
            raise ValueError('Turn condition not satisfied during the agenda check.')
        config = json.loads(self.con.execute('SELECT config FROM campaign WHERE id=1').fetchone()[0])
        from .mechanics import materialize as mechanical_materialize
        event=mechanical_materialize(state,event,config)
        if event.get('asset_operations'):
            from .possessions import materialize
            config = json.loads(self.con.execute('SELECT config FROM campaign WHERE id=1').fetchone()[0])
            event = materialize(state, event, config)
        for change in event['changes']:
            group,key=pointer(state,change['path'])
            if change['operation']=='set':group[key]=deepcopy(change['value'])
            elif change['operation']=='increment':
                if type(group.get(key)) is not int or type(change['value']) is not int:raise ValueError('Non-numeric increment')
                group[key]+=change['value']
            else:
                if key not in group:raise ValueError('Cannot delete a value that does not exist')
                del group[key]
        state['world_minute']=event['world_minute']
        if config.get('mechanics'):state['world_second']=event.get('world_second',0)

    def _source_audience(self, reference, events):
        source=next((e for e in events if e['event_id']==reference),None)
        if reference.startswith('agenda:'):
            source=self.get(reference.removeprefix('agenda:'))
        elif reference.startswith(('plan:','canon:','chronicle:','arc:','arc_fact:')):
            source={'audience':['narrator']}
        elif source is None:
            row=self.con.execute('SELECT payload FROM events WHERE event_id=?',(reference,)).fetchone()
            if row:source=json.loads(row[0])
            elif self.con.execute("SELECT 1 FROM sqlite_master WHERE name='memory_sources'").fetchone():
                row=self.con.execute('SELECT audience FROM memory_sources WHERE reference=?',(reference,)).fetchone()
                if row:source={'audience':json.loads(row[0])}
        return set(source['audience']) if source is not None else None

    def normalize_audiences(self, proposal, updates, commands=False):
        """Restrict the backstage follow-ups, without changing the facts or their audiences."""
        normalized=deepcopy(updates)
        corrections=[]
        for update in normalized:
            before=list(update['audience'])
            permitted=set(before)
            constraints=[]
            old=self.get(update['entry_id'])
            if old:
                permitted.intersection_update(old['audience'])
                constraints.append(('agenda:'+update['entry_id'],set(old['audience'])))
            for reference in update['references']:
                audience=self._source_audience(reference,proposal['events'])
                if audience is not None:
                    permitted.intersection_update(audience)
                    constraints.append((reference,audience))
            if permitted==set(before):continue
            identifier=update['entry_id']
            if 'narrator' not in permitted:
                raise ValueError('Incompatible agenda audience: '+identifier+'; the narrator must stay authorized.')
            index=update['outcome_event_index']
            outcome=(proposal['events'][index] if type(index) is int and 0<=index<len(proposal['events']) else None)
            if not commands and (update['interrupt'] or (old and old['interrupt'])):
                raise ValueError('Automatic audience reduction refused for an interruption: '+identifier)
            manifestation = commands and manifests(old or update) and update['status'] in ('done','finished')
            visible = permitted | ({'player'} if manifestation else set())
            if outcome and not set(outcome['audience'])<=visible:
                raise ValueError('Automatic audience reduction contradicts the resolution event: '+identifier)
            update['audience']=[recipient for recipient in before if recipient in permitted]
            corrections.append({'entry_id':identifier,'audience_before':before,
                'audience_after':list(update['audience']),
                'limiting_sources':[ref for ref,audience in constraints if not set(before)<=audience]})
        return normalized,corrections

    @staticmethod
    def _early_verification_closure(proposal, old, update, outcome, state):
        """A step taken by the player can close a follow-up before its check."""
        return bool(old and outcome and old['kind']=='verification' and
            update['kind']=='verification' and update['status']=='finished' and
            not old['interrupt'] and not update['interrupt'] and
            state['world_minute']<=outcome['world_minute']<old['due_minute'] and
            'action:'+proposal['request_id'] in outcome['causes'])

    def normalize_resolution_dates(self, proposal, updates, state):
        """Keep the planned date if the model copied the resolution date into it."""
        normalized=deepcopy(updates)
        corrections=[]
        for update in normalized:
            old=self.get(update['entry_id'])
            if not old or old['status'] not in ACTIVE or update['expected_revision']!=old['revision']:
                continue
            if update['status'] not in ('done','finished','cancelled') or update['due_minute']==old['due_minute']:
                continue
            index=update['outcome_event_index']
            outcome=(proposal['events'][index] if type(index) is int and 0<=index<len(proposal['events']) else None)
            if not outcome or update['due_minute']!=outcome['world_minute']:
                continue
            when=outcome['world_minute']
            early=self._early_verification_closure(proposal,old,update,outcome,state)
            if when!=max(state['world_minute'],old['due_minute']) and not early:
                continue
            corrections.append({'entry_id':update['entry_id'],'due_minute_before':update['due_minute'],
                'due_minute_after':old['due_minute'],'resolution_minute':when})
            update['due_minute']=old['due_minute']
        return normalized,corrections

    def _is_postponed_follow_up(self, identifier):
        """True for a follow-up already postponed, non-interrupting, outside the chronicle and agreements."""
        if identifier.startswith((CHRONICLE_PREFIX,'accord:')):return False
        row=self.con.execute('SELECT status FROM world_agenda WHERE entry_id=?',(identifier,)).fetchone()
        entry=self.get(identifier)
        return bool(row and row['status']=='postponed' and entry and not entry['interrupt'])

    def _deferrable_long_activity(self, identifier):
        """Non-interrupting backstage follow-up that may stay due during a long activity of the player."""
        if identifier.startswith((CHRONICLE_PREFIX,'accord:')):return False
        entry=self.get(identifier)
        if not entry or entry['interrupt'] or entry['kind'] not in ('project','verification','risk'):return False
        return self._long_activity_turn()

    def _overdue_without_time_passing(self, identifier, state, end):
        """Non-interrupting entry (outside chronicle and agreements) already late, in a turn that does not advance time."""
        if identifier.startswith((CHRONICLE_PREFIX,'accord:')) or end>state['world_minute']:return False
        entry=self.get(identifier)
        return bool(entry and not entry['interrupt'] and entry['due_minute']<=state['world_minute'])

    def _missed_before_turn(self, identifier, state):
        """Non-interrupting entry (outside chronicle and agreements) whose deadline had already passed when
        this turn began. An earlier turn let it slip; refusing every later turn that moves the clock would
        block the campaign. Interruptions, the chronicle and agreements stay strict."""
        if identifier.startswith((CHRONICLE_PREFIX,'accord:')):return False
        entry=self.get(identifier)
        return bool(entry and not entry['interrupt'] and entry['due_minute']<state['world_minute'])

    def _due_at_review_stop(self, identifier, end):
        """Non-interrupting backstage entry (outside chronicle and agreements) due exactly where the turn stops."""
        if identifier.startswith((CHRONICLE_PREFIX,'accord:')):return False
        entry=self.get(identifier)
        return bool(entry and not entry['interrupt'] and 'player' not in entry['audience'] and entry['due_minute']==end)

    def _due_when_turn_starts(self, identifier, state):
        """Non-interrupting backstage entry (outside chronicle and agreements) due exactly at the turn's start."""
        if identifier.startswith((CHRONICLE_PREFIX,'accord:')):return False
        entry=self.get(identifier)
        return bool(entry and not entry['interrupt'] and 'player' not in entry['audience']
                    and entry['due_minute']==state['world_minute'])

    def _effective_events(self, events, state):
        row=self.con.execute('SELECT config FROM campaign WHERE id=1').fetchone()
        if not row:return list(events)
        from .mechanics import effective_events
        return effective_events(state,events,json.loads(row[0]))

    def _tracked_npc_step(self, identifier):
        """Off-screen step of a tracked NPC (npc_step, npc_step2, ...): quiet, no player in the audience.

        Several of them come due on the same minute and the narrator tends to forget some, which refused
        every turn that moved the clock. They stay due and are proposed late at the next turn instead."""
        if not re.match(r'npc_step\d*:',identifier):return False
        entry=self.get(identifier)
        return bool(entry and not entry['interrupt'] and entry['kind']=='project' and 'player' not in entry['audience'])

    def _long_activity_turn(self):
        row=self.con.execute('SELECT payload FROM session').fetchone()
        pending=(json.loads(row[0]).get('pending_action') if row else None) or {}
        from .planner import long_activity
        return long_activity(pending.get('text',''))

    @staticmethod
    def normalize_chronicle_manifestations(proposal, updates):
        """A chronicle sign closed on a purely observable event the narrator left private is shown to the player.

        Only an event with no change, mechanic or consequence is widened: nothing secret can leak through it."""
        corrections=[]
        for update in updates:
            index=update['outcome_event_index']
            if (update['status'] not in ('done','finished') or not update['entry_id'].startswith(CHRONICLE_PREFIX)
                    or type(index) is not int or not 0<=index<len(proposal['events'])):
                continue
            event=proposal['events'][index]
            if 'player' in event['audience'] or any(event.get(key) for key in
                    ('changes','mechanical_operations','public_consequences','asset_operations')):
                continue
            event['audience']=[*event['audience'],'player']
            corrections.append({'entry_id':update['entry_id'],'event_index':index,'audience_added':'player'})
        return corrections

    def normalize_followup_dates(self, proposal, updates, state):
        """During a long activity, a new backstage follow-up dated inside the jump moves after its stopping point."""
        normalized=deepcopy(updates)
        corrections=[]
        if not proposal['events'] or not self._long_activity_turn():
            return normalized,corrections
        end=self._effective_events(proposal['events'],state)[-1]['world_minute']
        for update in normalized:
            if (update['status']!='planned' or update['due_minute']>end or update['interrupt']
                    or update['kind'] not in ('project','verification','risk') or 'player' in update['audience']
                    or update['entry_id'].startswith((CHRONICLE_PREFIX,'accord:')) or self.get(update['entry_id'])):
                continue
            # Keep the interval the narrator chose, counted from the end of the jump.
            interval=update['due_minute']-state['world_minute']
            moved=end+(interval if interval>0 else 1440)
            corrections.append({'entry_id':update['entry_id'],'due_minute_before':update['due_minute'],
                                'due_minute_after':moved,'stopping_minute':end})
            update['due_minute']=moved
        return normalized,corrections

    def normalize_arc_horizon(self, proposal, updates, state):
        """A scene that runs a little past the next world arcs review stops at the review instead of being refused.

        The narrator often ends a scene some minutes after the review date it was told to respect, and every
        retry fails the same way. Only a small overshoot is brought back: a long ellipsis still has to be cut
        by the player or the narrator. Mutates the events and the updates dated on them, like the other
        normalizations that work on the proposal."""
        corrections=[]
        events=proposal['events']
        if not events or proposal['blocking_questions']:
            return corrections
        from .arcs import ArcRegistry
        horizon=ArcRegistry(self.con).horizon(state['world_minute'])
        if horizon is None:
            return corrections
        end=self._effective_events(events,state)[-1]['world_minute']
        if end<=horizon or end-horizon>ARC_HORIZON_TOLERANCE:
            return corrections
        moved={}
        for index,event in enumerate(events):
            if event['world_minute']>horizon:
                moved[index]=event['world_minute']
                corrections.append({'event_index':index,'world_minute_before':event['world_minute'],
                                    'world_minute_after':horizon,'arc_horizon':horizon})
                event['world_minute']=horizon
                event['world_second']=0
        # If dated mechanical operations still stretch the turn, the validation refuses it as before.
        for update in updates:
            index=update.get('outcome_event_index')
            if index in moved and update['due_minute']==moved[index]:
                update['due_minute']=horizon
        return corrections

    def validate(self, proposal, updates, continuation, rolls, allowed, state, packet):
        """Refuse a jump beyond a forgotten deadline or an interruption."""
        check_json([updates,continuation,rolls])
        schemas=agenda_schema({'type':'object','additionalProperties':False,
            'properties':{'path':{'type':'string','pattern':'^/('+'|'.join(sorted(GROUPS))+')/[^/]+$'},
                          'equals':{}},'required':['path','equals']})
        validate(schemas['agenda_updates'],updates)
        validate(schemas['continuation'],continuation)
        validate(schemas['roll_checks'],rolls)
        events=proposal['events']
        from .mechanics import instant
        start_instant=instant(state)
        # The real end of the turn: a mechanical operation dated after its event stretches it at application.
        effective=self._effective_events(events,state)
        end_instant=instant(effective[-1]) if events else start_instant
        end=effective[-1]['world_minute'] if events else state['world_minute']
        stated_end=events[-1]['world_minute'] if events else state['world_minute']
        if proposal['blocking_questions']:
            if updates or continuation or rolls:
                raise ValueError('A question to the player forbids agenda changes.')
            return
        # Check from SQLite, not from a possibly old or truncated packet.
        from .arcs import ArcRegistry
        arc_horizon=ArcRegistry(self.con).horizon(state['world_minute'])
        if arc_horizon is not None and end>arc_horizon:
            raise ValueError('Ellipsis too long: stop at the next review of the world arcs.')
        if len({u['entry_id'] for u in updates})!=len(updates):
            raise ValueError('Two modifications of the same future event.')
        known={e['entry_id']:e for e in packet['events']}
        # Targeted reading through the deadline index, without scanning the history.
        due=self.con.execute('''SELECT entry_id FROM world_agenda WHERE status IN ('planned','postponed')
            AND due_minute<=? ORDER BY due_minute,entry_id''',(end,)).fetchall()
        resolved={u['entry_id']:u for u in updates if u['status']!='planned'}
        for row in due:
            identifier=row['entry_id']
            if identifier not in known:
                raise ValueError('Time jump too long for the provided agenda; stop before the unloaded deadlines.')
            if identifier not in resolved:
                if identifier.startswith(QUIET_NEWS_PREFIX):
                    from .scene_pacing import quiet_news_overtaken
                    # A fight or another interruption takes over: the scheduler resets the entry after the turn.
                    if quiet_news_overtaken(state,effective,updates,known[identifier]['due_minute'],continuation):
                        continue
                    raise ValueError('Forgotten deadline: '+identifier+'; show the news reaching the player.')
                # A routine step that is already postponed, without interruption, simply stays due:
                # it will be proposed late at the next turn instead of making this one rejected.
                if self._is_postponed_follow_up(identifier):
                    continue
                # Long activity: a non-interrupting backstage follow-up stays due and will be proposed late.
                if self._deferrable_long_activity(identifier) or self._tracked_npc_step(identifier):
                    continue
                # Already overdue before this turn and the clock does not move: the narrator cannot have
                # "jumped" over it, so refusing would block every turn forever. It stays due and is proposed again.
                if self._overdue_without_time_passing(identifier,state,end):
                    continue
                # Already missed by an earlier turn: this turn did not jump over it. It stays due and is proposed again.
                if self._missed_before_turn(identifier,state):
                    continue
                # The turn stops exactly at the arcs review and a quiet backstage step comes due on the same
                # minute: the narrator was told to stop there, not to settle it. It stays due and is proposed again.
                if arc_horizon is not None and end==arc_horizon and self._due_at_review_stop(identifier,end):
                    continue
                # A quiet backstage step that comes due on the very minute this turn starts (the previous turn
                # stopped there, e.g. at an arcs review) and that the narrator left alone: it stays due.
                if self._due_when_turn_starts(identifier,state):
                    continue
                due_minute=self.get(identifier)['due_minute']
                if stated_end<due_minute<=end:
                    raise ValueError('Forgotten deadline: '+identifier+' (due minute '+str(due_minute)
                        +'; the mechanical operations move the end of the turn from minute '+str(stated_end)
                        +' to minute '+str(end)+'. Resolve or postpone it, or date the operations before it.)')
                raise ValueError('Forgotten deadline: '+identifier)
        interruptions=[]
        for update in updates:
            if len(check_json(update))>6000:raise ValueError('Agenda entry too large.')
            old=self.get(update['entry_id'])
            if (old is None and update['expected_revision']!=-1) or (old and update['expected_revision']!=old['revision']):
                raise ValueError('Obsolete agenda revision: '+update['entry_id'])
            if old is None and update['status']=='postponed':
                raise ValueError('A postponement must concern an existing deadline.')
            if old and update['entry_id'] not in known:
                raise ValueError('Future event missing from the authorized context.')
            if old and old['status'] not in ACTIVE:
                raise ValueError('Future event already finished.')
            quiet_news=update['entry_id'].startswith(QUIET_NEWS_PREFIX)
            if quiet_news:
                if old is None:
                    raise ValueError('Quiet news entries are created by the pacing scheduler.')
                if update['status'] in ('planned','postponed','cancelled'):
                    raise ValueError('Keep this pacing deadline. Show it as due, or leave it alone if a fight '
                                     'or another urgent event has already taken over.')
            if update['entry_id'].startswith(CHRONICLE_PREFIX):
                self._check_chronicle(old,update)
            for condition in update['conditions']:
                pointer(state,condition['path'])
            if any(ref not in allowed for ref in update['references']):
                raise ValueError('Agenda origin without an authorized reference.')
            if 'narrator' not in update['audience']:
                raise ValueError('The narrator must have access to each future event.')
            # The titles, goals and places of a secret entry do not become public.
            if old and not set(update['audience'])<=set(old['audience']):
                raise ValueError('Widening the audience of a future event is forbidden.')
            for reference in update['references']:
                audience=self._source_audience(reference,events)
                if audience is not None and not set(update['audience'])<=audience:
                    raise ValueError('A private source cannot produce a public entry.')
            index=update['outcome_event_index']
            outcome=events[index] if type(index) is int and index<len(events) else None
            if type(index) is int and outcome is None:
                raise ValueError('Resolution event missing.')
            if update['status'] in ACTIVE and update['due_minute']<=end:
                raise ValueError('A next deadline must be strictly in the future.')
            if old and update['status']=='planned' and update['due_minute']!=old['due_minute']:
                raise ValueError('A date change must be a justified postponement.')
            if old and update['status'] in ('done','finished','cancelled') and update['due_minute']!=old['due_minute']:
                raise ValueError('A resolution keeps the date of its original deadline.')
            if update['status']!='planned' and outcome is None:
                raise ValueError('Resolution or postponement without a recorded event.')
            if outcome:
                ref='agenda:'+update['entry_id']
                if old and ref not in outcome['causes']:
                    raise ValueError('Resolution without a link to its deadline.')
                when=outcome['world_minute']
                when_instant=instant(outcome)
                early=self._early_verification_closure(proposal,old,update,outcome,state)
                if old and old['due_minute']<=end and when_instant!=max(start_instant,old['due_minute']*60) and not early:
                    expected_minute, expected_second = divmod(max(start_instant, old['due_minute'] * 60), 60)
                    raise ValueError('A crossed deadline must be handled at its date, not at the end of the jump. '
                                     f"Entry: {update['entry_id']}. Expected minute {expected_minute}, second "
                                     f"{expected_second}. Event {index} has minute {when}, second {outcome.get('world_second', 0)}.")
                if old and when_instant<max(start_instant,old['due_minute']*60) and update['status'] in ('done','finished') and not early:
                    raise ValueError('Future event realized before its deadline: '
                                     + update['entry_id'] + '; resolution minute ' + str(when)
                                     + ', deadline minute ' + str(old['due_minute']) + '.')
                # At the planned status, the event motivates the creation of the future follow-up.
                # Only a resolution must coincide with the date of this step.
                if old is None and update['status']!='planned' and (when!=update['due_minute'] or when<state['world_minute']):
                    raise ValueError('A step created and resolved within the turn must respect its fictional date.')
                planned=old or update
                manifestation=(manifests(planned) and update['status'] in ('done','finished'))
                visible_audience=set(update['audience'])|(
                    {'player'}|{r for r in outcome['audience'] if r.startswith('npc:')} if manifestation else set())
                if not set(outcome['audience'])<=visible_audience:
                    raise ValueError('Resolution of a secret event with an unauthorized audience: '
                                     +update['entry_id']+'; event '+str(index)
                                     +'. Separate the observable fact and the private closing of the follow-up.')
                at=deepcopy(state)
                for preceding in events[:index]:self._evolve(at,preceding)
                if any(e['event_id'] in update['references'] and instant(e)>when_instant for e in events):
                    raise ValueError('A resolution cannot depend on a future fact within this turn.')
                if update['status'] in ('done','finished') and not conditions_hold(at,planned['conditions']):
                    raise ValueError('Project conditions not met; cancel or postpone with a cause.')
                if (update['status'] in ('done','finished')
                        and planned['entry_id'].startswith((CHRONICLE_PREFIX,QUIET_NEWS_PREFIX))
                        and 'player' not in outcome['audience']):
                    label='Quiet news' if quiet_news else 'Chronicle manifestation'
                    raise ValueError(label+' not perceived by the player: '+update['entry_id']+
                                     '. The event '+str(index)+' must show the sign or the event to the player character.')
                if update['status'] in ('done','finished') and planned['interrupt']:
                    if 'player' not in outcome['audience']:
                        raise ValueError('Interruption not presented to the player at its deadline. The event '+str(index)+(' is private; outcome_event_index must point to the observable manifestation, without '
                                                                        'making a secret closing public.'))
                    if when_instant!=max(start_instant,planned['due_minute']*60):
                        raise ValueError(('Interruption not presented to the player at its deadline. The date of its manifestation '
                                          'does not match the deadline.'))
                    interruptions.append(when_instant)
                if old and old['entry_id'].startswith('accord:') and update['status']=='finished':
                    self._evolve(at,outcome)
                    key=old['entry_id'].removeprefix('accord:')
                    commitment=at['commitments'].get(key)
                    if commitment and commitment['status']=='open':
                        raise ValueError('An agreement that is still open must keep a next check. '
                                         f'Entry: {old["entry_id"]}. '
                                         f'If the recorded facts settle it, set the full /commitments/{key} object '
                                         'to kept or broken in the outcome event. If it is still pending, postpone '
                                         'this check with a reason and a date after the turn ends.')
            planned=old or update
            if update['status']=='done' and planned['kind'] in ('project','verification'):
                successors=[u for u in updates if u['entry_id']!=planned['entry_id'] and u['actor']==planned['actor']
                            and u['status'] in (*ACTIVE,'done','finished') and
                            u['due_minute']>planned['due_minute'] and
                            ('agenda:'+planned['entry_id'] in u['references'] or
                             outcome['event_id'] in u['references'])]
                if not successors:
                    raise ValueError((('A completed step must have a next step for its actor, or close the matter explicitly with '
                                       'the status finished. Entry: '))+planned['entry_id']+'; actor: '+planned['actor']+
                                     '; expected link: agenda:'+planned['entry_id']+' or event '+outcome['event_id']+'.')
        if interruptions and end_instant!=min(interruptions):
            raise ValueError('The turn goes past an interruption requiring a choice from the player.')
        if interruptions and (continuation is None or continuation['status'] not in ('suspended','finished')):
            raise ValueError(("An interruption must keep the player's goal, suspended or finished. Fill continuation "
                              "with goal (initial goal), remaining (what is left to do), status=suspended and "
                              "event_index pointing to the perceivable manifestation at the end of the turn. If this "
                              "goal is already completed, use status=finished and event_index=null. "
                              "continuation=null is not suitable when an interrupting deadline is resolved."))
        if continuation is not None and continuation['status']=='suspended':
            index=continuation['event_index']
            if type(index) is not int or index>=len(events) or 'player' not in events[index]['audience']:
                raise ValueError(('Interrupted goal without a recorded perceivable event: status=suspended requires an '
                                  'integer event_index pointing to an event whose audience includes player; otherwise '
                                  'continuation=null.'))
            if instant(events[index])!=end_instant:
                raise ValueError('The turn goes past its situational interruption.')
        elif continuation is not None and continuation['event_index'] is not None:
            raise ValueError('A finished or abandoned goal does not point to an interruption.')
        used=set()
        for roll in rolls:
            if roll['key'] in used:raise ValueError('Roll used several times within the same turn.')
            used.add(roll['key'])
            value=packet['d100_rolls'].get(roll['key'])
            if value is None or roll['success']!=(value<=roll['threshold']):
                raise ValueError('Roll result incompatible with the persistent value.')

    def _check_chronicle(self, old, update):
        """The chronicler's manifestations are not skipped: only a bounded postponement is allowed."""
        if old is None:
            raise ValueError('The chronicle:* deadlines are created by the chronicler, not by the narrator: '+update['entry_id']+'. A follow-up to a manifestation takes another prefix (for example world:).')
        if update['status']=='cancelled':
            raise ValueError('A chronicle manifestation cannot be cancelled: '+update['entry_id']+
                             '. Show it to the player character, or postpone it with a cause if they cannot perceive it.')
        if update['status']=='postponed':
            reports=self.con.execute("SELECT COUNT(*) FROM agenda_history WHERE entry_id=? "
                "AND json_extract(payload,'$.status')='postponed'",(update['entry_id'],)).fetchone()[0]
            if reports>=CHRONICLE_REPORTS:
                raise ValueError('Chronicle manifestation already postponed twice: '+update['entry_id']+
                                 '; it must now reach the player character.')
            if update['due_minute']>old['due_minute']+CHRONICLE_REPORT_DELAY:
                raise ValueError('A chronicle postponement does not exceed two days: '+update['entry_id'])
        if update['interrupt']!=old['interrupt'] or update['audience']!=old['audience']:
            raise ValueError('A chronicle manifestation keeps its interruption and its audience.')

    def apply(self, request_id, updates, continuation, rolls, minute):
        payload=check_json({'updates':updates,'continuation':continuation,'roll_checks':rolls})
        receipt=self.con.execute('SELECT payload FROM agenda_receipts WHERE request_id=?',(request_id,)).fetchone()
        if receipt:
            if receipt[0]!=payload:raise ValueError('Different agenda for a request already recorded.')
            return
        for update in updates:
            old=self.get(update['entry_id'])
            self._put(update,request_id,minute,old['revision']+1 if old else 0)
        # A dialogue or another action does not erase a task that is still suspended.
        if continuation is not None:
            self.con.execute('INSERT INTO agenda_continuations VALUES (?,?,?)',
                             (request_id,minute,check_json(continuation)))
        self.con.execute('INSERT INTO agenda_receipts VALUES (?,?)',(request_id,payload))

