"""Idempotent state, events and results in a single SQLite transaction."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from .contracts import ROOT, check, check_json
from . import schema

GROUPS = {'positions', 'knowledge', 'intentions', 'resources', 'beliefs',
          'inventory', 'effects', 'relations', 'powers', 'commitments', 'projects'}


def pointer(state, path):
    parts = path.split('/')
    if len(parts) != 3 or parts[0] or parts[1] not in GROUPS or not parts[2]:
        raise ValueError('Modification path forbidden')
    return state.setdefault(parts[1], {}), parts[2]


def conditions_hold(state, conditions):
    for condition in conditions:
        group, key = pointer(state, condition['path'])
        if key not in group or group[key] != condition['equals']:
            return False
    return True


class Campaign:
    def __init__(self, path):
        self.path = Path(path)
        resolved = self.path.resolve()
        if any(resolved.is_relative_to(ROOT / folder) for folder in ('canon','corpus','sources')):
            raise ValueError('A campaign cannot write to the sources or the canon')

    @contextmanager
    def transaction(self):
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        try:
            con.execute('BEGIN IMMEDIATE')
            schema.check_supported(con)
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def create(self, state, config):
        check('state', state)
        if self.path.exists():
            raise ValueError('File exists; initialization refused')
        config = {**deepcopy(config), 'initial_state':deepcopy(state)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path)
        try:
            con.execute('PRAGMA journal_mode=WAL')
            con.executescript('''
                CREATE TABLE IF NOT EXISTS campaign (id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL, config TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS requests (request_id TEXT PRIMARY KEY, digest TEXT NOT NULL, result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, state_version INTEGER NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cycles (cycle_id TEXT PRIMARY KEY, active INTEGER NOT NULL, max_tasks INTEGER NOT NULL, max_calls INTEGER NOT NULL, calls INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY, cycle_id TEXT NOT NULL, spec TEXT NOT NULL, status TEXT NOT NULL, proposal TEXT, reason TEXT NOT NULL DEFAULT '');
            ''')
            con.execute('BEGIN IMMEDIATE')
            if con.execute('SELECT 1 FROM campaign').fetchone():
                raise ValueError('The campaign already exists; use its save')
            con.execute('INSERT INTO campaign VALUES (1,?,?)', (check_json(state), check_json(config)))
            schema.migrate(con)
            con.commit()
        finally:
            con.close()
        return self

    def state(self):
        with self.transaction() as con:
            return self._state(con)

    def snapshot(self, recipient):
        with self.transaction() as con:
            return self._snapshot(con, recipient)

    def _snapshot(self, con, recipient):
        state = self._state(con)
        config = json.loads(con.execute('SELECT config FROM campaign WHERE id=1').fetchone()[0])
        events = [json.loads(row[0]) for row in con.execute('SELECT payload FROM events ORDER BY rowid')]
        return state,config,[e for e in events if recipient in e['audience']]

    def config(self):
        with self.transaction() as con:
            return json.loads(con.execute('SELECT config FROM campaign WHERE id=1').fetchone()[0])

    def _state(self, con):
        row = con.execute('SELECT state FROM campaign WHERE id=1').fetchone()
        if not row:
            raise ValueError('Campaign not initialized')
        return json.loads(row[0])

    def _session(self, con):
        # Additive migration, also for saves created before the exact resume.
        con.execute('CREATE TABLE IF NOT EXISTS session (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
        con.execute('CREATE TABLE IF NOT EXISTS exchanges (seq INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT UNIQUE NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL)')
        initial = {'phase':'no_scene', 'scene':'', 'pending_action':None, 'last_request_id':None}
        con.execute('INSERT OR IGNORE INTO session VALUES (1,?)', (check_json(initial),))
        return json.loads(con.execute('SELECT payload FROM session WHERE id=1').fetchone()[0])

    def _save_session(self, con, session):
        con.execute('UPDATE session SET payload=? WHERE id=1', (check_json(session),))

    def open_scene(self, text):
        """Record the initial scene only once, before displaying it."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Scene text expected')
        with self.transaction() as con:
            session = self._session(con)
            if session['phase'] == 'no_scene':
                session.update(phase='awaiting_player', scene=text)
                con.execute('INSERT INTO exchanges(message_id,role,content) VALUES (?,?,?)',
                            ('initial_scene','narrator',text))
                self._save_session(con,session)
            return session

    def begin_action(self, text, request_id=None):
        """Save the input before the computation; an interruption keeps its identifier."""
        with self.transaction() as con:
            return self._begin_action(con, text, request_id)

    def _begin_action(self, con, text, request_id=None):
        if not isinstance(text,str) or not text.strip():
            raise ValueError('Non-empty action expected')
        session = self._session(con)
        pending = session['pending_action']
        if pending:
            if pending['text'] == text and (request_id is None or request_id == pending['request_id']):
                return pending
            raise ValueError('An action remains to be resolved; resume its identifier before another action')
        request_id = request_id or uuid.uuid4().hex
        if con.execute('SELECT 1 FROM requests WHERE request_id=?',(request_id,)).fetchone():
            raise ValueError('Request already resolved')
        pending = {'request_id':request_id, 'text':text, 'state_version_at_input':self._state(con)['state_version']}
        con.execute('INSERT INTO exchanges(message_id,role,content) VALUES (?,?,?)',
                    (request_id+':player','player',text))
        session.update(phase='action_to_resolve',pending_action=pending)
        self._save_session(con,session)
        return pending

    def _record_narration(self, con, result):
        if result['status'] not in ('recorded','question'):
            return
        session = self._session(con)
        pending = session['pending_action']
        if pending and pending['request_id'] != result['request_id']:
            raise ValueError('Result differs from the pending action')
        if result['narration']:
            con.execute('INSERT OR IGNORE INTO exchanges(message_id,role,content) VALUES (?,?,?)',
                        (result['request_id']+':narrator','narrator',result['narration']))
            session['scene'] = result['narration']
        session.update(phase='awaiting_player',pending_action=None,last_request_id=result['request_id'])
        self._save_session(con,session)

    def reevaluate_action(self):
        """A final rejection keeps its result; the re-evaluation receives a new identifier."""
        with self.transaction() as con:
            return self._reevaluate_action(con)

    def _reevaluate_action(self, con):
        session=self._session(con)
        pending=session['pending_action']
        if not pending:
            raise ValueError('No pending action')
        row=con.execute('SELECT result FROM requests WHERE request_id=?',(pending['request_id'],)).fetchone()
        if not row or json.loads(row[0])['status'] != 'rejected':
            raise ValueError('Re-evaluation reserved to a rejected action; otherwise resume its identifier')
        pending['previous_request_ids']=pending.get('previous_request_ids',[])+[pending['request_id']]
        pending['request_id']=uuid.uuid4().hex
        pending['state_version_at_input']=self._state(con)['state_version']
        self._save_session(con,session)
        return pending

    def resume(self, limit=12):
        with self.transaction() as con:
            return self._resume(con, limit)

    def _resume(self, con, limit=12):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('Limit between 1 and 200 expected')
        state = self._state(con)
        session = self._session(con)
        # Presentation receipts may refer to private matters of the narrator.
        session.pop('place_reminders', None)
        rows = con.execute('SELECT seq,role,content FROM exchanges ORDER BY seq DESC LIMIT ?', (limit,)).fetchall()
        return {'campaign_id':state['campaign_id'], 'state_version':state['state_version'],
                'world_minute':state['world_minute'], 'position':state['positions'].get('player'),
                'session':session, 'recent_exchanges':[dict(row) for row in reversed(rows)],
                'exchange_count':con.execute('SELECT COUNT(*) FROM exchanges').fetchone()[0]}

    def prepare_turn(self, text=None, query='', exchange_limit=12, context_limit=100, reevaluate=False,
                     narrative_memory=False):
        """Private preparation in one call, with no generation and no fictional progress."""
        from .memory import ContextBuilder
        from .indexed_memory import MemoryIndex
        with self.transaction() as con:
            if text is not None:
                self._begin_action(con, text)
            if reevaluate:
                self._reevaluate_action(con)
            resumed = self._resume(con, exchange_limit)
            pending = resumed['session']['pending_action']
            memory = None
            if narrative_memory:
                state = self._state(con)
                config = json.loads(con.execute('SELECT config FROM campaign WHERE id=1').fetchone()[0])
                index = MemoryIndex(con)
                index.sync()
                action = pending['text'] if pending else ''
                answers = [item['content'] for item in resumed['recent_exchanges'] if item['role']=='narrator']
                recovery = (con.execute('SELECT query FROM memory_recovery WHERE request_id=?',
                            (pending['request_id'],)).fetchone() if pending else None)
                search_query = query or (recovery[0] if recovery else '')
                memory = index.packet(action,answers[-1] if answers else '',state['positions'].get('player',''),
                    query=search_query,recent_seqs={item['seq'] for item in resumed['recent_exchanges']},
                    budget=32000 if recovery else 16000,excerpt_chars=6000 if recovery else 1800)
                journal = index.journal_for(memory)
            else:
                state, config, journal = self._snapshot(con, 'narrator')
            context = ContextBuilder(self).build_from_snapshot(
                (state, config, journal), 'narrator', query,
                pending['text'] if pending else '', context_limit)
            previous = None
            if pending:
                row = con.execute('SELECT result FROM requests WHERE request_id=?', (pending['request_id'],)).fetchone()
                previous = json.loads(row[0]) if row else None
            status = 'rejected_action' if previous else ('ready' if pending else 'no_action')
            template = None
            if pending and not previous:
                template = {'schema_version':'0.2', 'request_id':pending['request_id'],
                            'campaign_id':state['campaign_id'], 'expected_state_version':state['state_version'],
                            'narration':'', 'events':[], 'blocking_questions':[]}
            prepared = {'recipient':'narrator', 'status':status, 'resume':resumed,
                    'state':state, 'context':context, 'model_proposal':template,
                    'previous_result':previous,
                    'campaign_rules':{key:config[key] for key in (
                        'rule_ids','resource_owners','seed_references','solar_schedule','rules_pending_details','spell_cost_rules','character_rules','character_sheet','player_description','possession_rules','mechanics','creation_format','recovery_policy','narration_language','world_rules')
                        if key in config},
                    'narrator_preparation':config.get('private_preparation')}
            if memory is not None:
                prepared['narrative_memory_context'] = memory
            from .creations import CreationRegistry
            prepared['campaign_creations'] = CreationRegistry(con).packet(
                (pending['text'] if pending else '')+' '+query+' '+state['positions'].get('player',''))
            from .npc_sheets import project as npc_sheets
            prepared['npc_sheets'], prepared['npc_diagnostic'] = npc_sheets(
                state, CreationRegistry(con).latest(), pending['text'] if pending else '',
                resumed['session']['scene'])
            from .agenda import Agenda
            agenda=Agenda(con)
            agenda.sync_commitments(state)
            prepared['world_agenda']=agenda.packet(state,pending)
            from .place_reminders import project as location_reminders
            prepared['place_reminders'], prepared['place_reminders_diagnostic'] = location_reminders(
                state, prepared['world_agenda'], self._session(con))
            from .planner import WorldPlanner
            prepared['world_planning']=WorldPlanner(con).narrator_packet(
                pending['text'] if pending else '',state['positions'].get('player',''))
            from .canon_selection import select_for_turn,save_context
            prior=[]
            if pending and con.execute("SELECT 1 FROM sqlite_master WHERE name='canon_selection_contexts'").fetchone():
                prior=[json.loads(r[0]) for r in con.execute(
                    'SELECT payload FROM canon_selection_contexts WHERE request_id=? AND state_version=? '
                    'AND canon_revision=? AND role=? ORDER BY rowid DESC',
                    (pending['request_id'],state['state_version'],state['versions']['canon_revision'],'narrator'))]
            canonical=select_for_turn(state,config,pending['text'] if pending else '',
                resumed['session']['scene'],search_query if narrative_memory else query,previous=prior)
            if canonical is not None:
                prepared['documentary_canon']=canonical
                if status=='ready':save_context(con,pending['request_id'],state,'narrator',canonical)
            return prepared

    def apply(self, proposal, background=False, memory_updates=None, agenda_changes=None, expected_versions=None,
              creation_updates=None, location_reminders=None):
        check('proposal', proposal)
        with self.transaction() as con:
            from .creations import CreationRegistry
            registry = CreationRegistry(con)
            creations = creation_updates or []
            config = json.loads(con.execute('SELECT config FROM campaign WHERE id=1').fetchone()[0])
            recorded=registry.recorded(creations,proposal['request_id']) if creations else False
            if creations and ((config.get('creation_format') != '1' and not recorded) or background):
                raise ValueError('Creations format 1 not installed or background entry not supported.')
            from .indexed_memory import MemoryIndex
            if creations:
                MemoryIndex(con).sync()
                registry.validate(creations, proposal)
            if expected_versions is not None and self._state(con)['versions']!=expected_versions:
                raise ValueError('Documentary versions modified; proposal kept, an explicit retry is needed.')
            from .agenda import Agenda
            agenda=Agenda(con)
            changes=agenda_changes or {'updates':[],'continuation':None,'roll_checks':[]}
            if agenda_changes is not None:agenda.initialize()
            from .planner import WorldPlanner
            planner=WorldPlanner(con)
            reminder_before = self._state(con)
            was_recorded = con.execute('SELECT 1 FROM requests WHERE request_id=?',(proposal['request_id'],)).fetchone()
            before=reminder_before if planner.exists() else None
            result = self._apply(con, proposal, background,changes)
            from .indexed_memory import MemoryIndex
            index = MemoryIndex(con)
            if index.exists():
                index.sync()
                if result['status']=='recorded' and memory_updates is not None:
                    index.apply_updates(memory_updates,proposal['request_id'])
            if agenda.exists() and result['status']=='recorded':
                agenda.apply(proposal['request_id'],changes['updates'],changes['continuation'],
                             changes['roll_checks'],result['world_minute_after'])
                agenda.sync_commitments(self._state(con))
            if not background:
                planner.record_turn(proposal,result,before)
            if result['status']=='recorded':
                registry.apply(creations, proposal)
                planner.sync_projects()
                if not background and not was_recorded:
                    from .place_reminders import record as record_reminders
                    session = self._session(con)
                    record_reminders(session, reminder_before, self._state(con), result['applied_events'],
                                     location_reminders or [])
                    self._save_session(con, session)
            return result

    @staticmethod
    def config_refs(con,state,packet):
        config=json.loads(con.execute('SELECT config FROM campaign WHERE id=1').fetchone()[0])
        refs=set(config.get('seed_references',[]))|set(config.get('rule_ids',[]))
        refs.update(ref for group in ('inventory','effects','relations','powers','commitments','projects')
                    for item in state.get(group, {}).values() for ref in item.get('references',[]))
        refs.update(ref for currency in config.get('possession_rules', {}).get('currencies', {}).values()
                    for ref in currency.get('references', []))
        refs.update('agenda:'+item['entry_id'] for item in packet['events'])
        refs.update(ref for item in packet['events'] for ref in item['references'])
        return refs

    def world_agenda(self, recipient='player'):
        from .agenda import Agenda
        with self.transaction() as con:
            state=self._state(con)
            agenda=Agenda(con)
            agenda.sync_commitments(state)
            return {'status':'agenda',**agenda.packet(state,recipient=recipient)}

    def world_summaries(self, recipient='player'):
        from .planner import WorldPlanner
        with self.transaction() as con:
            return {'status':'summaries','summaries':WorldPlanner(con).summaries(recipient,10)}

    def possessions(self, recipient='player'):
        from .possessions import packet
        with self.transaction() as con:
            config = json.loads(con.execute('SELECT config FROM campaign WHERE id=1').fetchone()[0])
            return packet(self._state(con), config, recipient)

    def skills(self, query='', recipient='player'):
        from .abilities import packet
        with self.transaction() as con:
            config = json.loads(con.execute('SELECT config FROM campaign WHERE id=1').fetchone()[0])
            return packet(self._state(con), config, recipient, query)

    def creations(self, query='', recipient='player'):
        from .creations import CreationRegistry
        with self.transaction() as con:
            return {'status':'creations','creations':CreationRegistry(con).packet(query,recipient)}

    def recover_memory(self, request_id, query, reset=False):
        from .indexed_memory import MemoryIndex
        with self.transaction() as con:
            session = self._session(con)
            pending = session['pending_action']
            if not pending or pending['request_id'] != request_id:
                raise ValueError('Recovery action differs from the pending input')
            index = MemoryIndex(con)
            index.sync()
            # reset is kept for historical callers, never to erase the budget.
            from .recovery import RecoveryBudget
            budget=RecoveryBudget(con,request_id)
            if budget.search_exhausted():
                return budget.blocked('Search budget reached for this action.')
            state = self._state(con)
            config=json.loads(con.execute('SELECT config FROM campaign').fetchone()[0])
            prefix,_,key=query.partition(':')
            group={'object':'inventory','project':'projects','relation':'relations','power':'powers'}.get(prefix.strip())
            if group:
                value=state.get(group,{}).get(key.strip())
                count=budget.state()['searches']+1
                packet={'group':group,'key':key.strip(),'entity':value,
                        'proofs':[{'reference':prefix.strip()+':'+key.strip()}] if value is not None else []}
                new=budget.record_search(query,packet,value is not None)
                status='ready' if value is not None and new else 'blocked'
                reason='Local sheet found; /resume allows the next step.' if status=='ready' else 'Identifier missing or reminder repeated without progress.'
                con.execute('INSERT OR REPLACE INTO memory_recovery VALUES (?,?,?,?,?,?)',
                         (request_id,query,status,count,check_json(packet),reason))
                return {'status':'memory_required','request_id':request_id,'reason':reason,
                    'recovered_sources':int(value is not None),'search_status':status}
            if query.lstrip().startswith('canon:') and config.get('canon_selection',{}).get('enabled'):
                from .canon_selection import select_for_turn,save_context
                packet=select_for_turn(state,config,pending['text'],session['scene'],query=query)
                count=budget.state()['searches']+1
                satisfied=packet['search']['request_satisfied']
                new=budget.record_search(query,packet,satisfied)
                # Partial is distinct from ready; a single enriched generation can use these hints.
                status=('ready' if satisfied else 'partial') if new else 'to_clarify'
                reason=('Canonical references recovered locally. Use /resume to allow a new generation.'
                        if status=='ready' else 'Partial excerpts found; the documentary target remains to be checked on resume.'
                        if status=='partial' else 'Search without a new reference. Action kept; local diagnostic needed.')
                save_context(con,request_id,state,'narrator',packet)
                con.execute('INSERT OR REPLACE INTO memory_recovery VALUES (?,?,?,?,?,?)',
                            (request_id,query,status,count,check_json(packet),reason))
                return {'status':'memory_required','request_id':request_id,'reason':reason,
                        'recovered_sources':len(packet['facts']),'search_status':status}
            result=index.recover(request_id,query,session['scene'],state['positions'].get('player',''))
            row=con.execute('SELECT packet,status FROM memory_recovery WHERE request_id=?',(request_id,)).fetchone()
            budget.record_search(query,json.loads(row[0]),row[1]=='ready')
            con.execute('UPDATE memory_recovery SET count=? WHERE request_id=?',
                        (budget.state()['searches'],request_id))
            return result

    def memories(self, query='', recipient='player', topics=False):
        from .indexed_memory import MemoryIndex
        with self.transaction() as con:
            self._session(con)
            index = MemoryIndex(con)
            index.sync()
            state = self._state(con)
            if topics:
                output=[]
                for row in con.execute('SELECT topic_id,title FROM memory_topics ORDER BY topic_id'):
                    sources,_ = index._topic_sources(row['topic_id'],recipient)
                    if sources:
                        output.append({'id':row['topic_id'],'title':row['title'],
                                       'references':[item['reference'] for item in sources]})
                return {'status':'topics','topics':output}
            return {'status':'memories',**index.packet(query,position=state['positions'].get('player',''),
                                                       recipient=recipient)}

    def _apply(self, con, proposal, background=False, agenda_changes=None):
        digest = hashlib.sha256(json.dumps(proposal,ensure_ascii=False,sort_keys=True,allow_nan=False).encode('utf-8')).hexdigest()
        previous = con.execute('SELECT digest,result FROM requests WHERE request_id=?', (proposal['request_id'],)).fetchone()
        if previous:
            if previous['digest'] != digest:
                raise ValueError('Request identifier reused with other content')
            return json.loads(previous['result'])
        state = self._state(con)
        result = {'schema_version':'0.2', 'request_id':proposal['request_id'],
                  'campaign_id':state['campaign_id'], 'status':'rejected',
                  'state_version_before':state['state_version'], 'state_version_after':state['state_version'],
                  'world_minute_after':state['world_minute'], 'applied_events':[], 'narration':'', 'reason':''}
        reason = None
        if proposal['campaign_id'] != state['campaign_id']:
            reason = 'Different campaign'
        elif proposal['expected_state_version'] != state['state_version']:
            reason = 'Obsolete result; a re-evaluation is needed'
        elif proposal['blocking_questions']:
            result.update(status='question', narration=' '.join(proposal['blocking_questions']))
            reason = 'Clarification needed, state kept'
        elif background and any(e['world_minute'] > state['world_minute'] for e in proposal['events']):
            # A deferred result is not finalized: it can be rechecked later.
            result.update(status='deferred', reason='Fictional deadline not reached')
            return check('result', result)
        else:
            updated = deepcopy(state)
            try:
                from .agenda import Agenda
                agenda=Agenda(con)
                if any(cause.startswith('canon:') for e in proposal['events'] for cause in e['causes']):
                    from .canon_selection import validate_canon_causes
                    existing=self.config_refs(con,state,agenda.packet(state,self._session(con)['pending_action'])) if agenda.exists() else set()
                    validate_canon_causes(con,proposal,state,existing)
                if agenda.exists():
                    changes=agenda_changes or {'updates':[],'continuation':None,'roll_checks':[]}
                    pending=self._session(con)['pending_action']
                    packet=agenda.packet(state,pending)
                    allowed=set(self.config_refs(con,state,packet))
                    from .canon_selection import selected_references
                    allowed.update(selected_references(con,proposal['request_id'],state))
                    allowed.update(event['event_id'] for event in proposal['events'])
                    allowed.add('action:'+proposal['request_id'])
                    # Targeted check of the original sources for manual applications.
                    for update in changes['updates']:
                        for reference in update['references']:
                            if con.execute('SELECT 1 FROM events WHERE event_id=?',(reference,)).fetchone():
                                allowed.add(reference)
                            elif con.execute("SELECT 1 FROM sqlite_master WHERE name='memory_sources'").fetchone():
                                if con.execute('SELECT 1 FROM memory_sources WHERE reference=?',(reference,)).fetchone():
                                    allowed.add(reference)
                    agenda.validate(proposal,changes['updates'],changes['continuation'],
                                    changes['roll_checks'],allowed,state,packet)
                from .character import validate_character_transition
                config = json.loads(con.execute('SELECT config FROM campaign WHERE id=1').fetchone()[0])
                from .possessions import materialize, validate_transition, validate_narration
                resolved_events = []
                last_minute = state['world_minute']
                for event in proposal['events']:
                    before_assets = deepcopy(updated)
                    from .mechanics import materialize as mechanical_materialize
                    event = mechanical_materialize(updated,event,config)
                    event = materialize(updated, event, config)
                    from .cooldowns import validate_event
                    validate_event(updated, event, config)
                    before_event = deepcopy(updated) if config.get('character_rules') else None
                    if event['event_id'] in updated['applied_event_ids'] or con.execute('SELECT 1 FROM events WHERE event_id=?', (event['event_id'],)).fetchone():
                        raise ValueError('Event already recorded')
                    if event['world_minute'] < last_minute:
                        raise ValueError('Events out of order or in the past')
                    last_minute = event['world_minute']
                    if not conditions_hold(updated, event['conditions']):
                        raise ValueError('Condition not met')
                    for change in event['changes']:
                        group, key = pointer(updated, change['path'])
                        if change['operation'] == 'set':
                            group[key] = deepcopy(change['value'])
                        elif change['operation'] == 'increment':
                            if type(group.get(key)) is not int or type(change['value']) is not int:
                                raise ValueError('Non-numeric increment')
                            group[key] += change['value']
                        else:
                            if key not in group:
                                raise ValueError('Deletion of a missing value')
                            del group[key]
                    updated['world_minute'] = event['world_minute']
                    if config.get('mechanics'):
                        updated['world_second']=event.get('world_second',0)
                    if before_event is not None:
                        validate_character_transition(before_event, updated, config)
                    validate_transition(before_assets, updated, event, config)
                    resolved_events.append(event)
                    updated['applied_event_ids'].append(event['event_id'])
                updated['state_version'] += 1
                updated['world_minute'] = last_minute
                validate_character_transition(state, updated, config)
                from .narrative_coherence import render_state_links, validate_facts
                from .mechanics import validate_narration as validate_ability_narration
                narration = render_state_links(proposal['narration'], updated, config)
                visible_proposal = {**proposal, 'narration': narration}
                validate_ability_narration(visible_proposal, config)
                validate_narration(state, updated, visible_proposal, config)
                validate_facts(state, updated, visible_proposal, config)
                from .consequences import materialize_public, validate_public
                resolved_events = materialize_public(updated, resolved_events)
                validate_public(state,updated,resolved_events,config,agenda._source_audience)
                check('state', updated)
            except ValueError as exc:
                reason = str(exc)
            if reason is None:
                for event in resolved_events:
                    con.execute('INSERT INTO events VALUES (?,?,?)', (event['event_id'], updated['state_version'], check_json(event)))
                con.execute('UPDATE campaign SET state=? WHERE id=1', (check_json(updated),))
                result.update(status='recorded', state_version_after=updated['state_version'],
                              world_minute_after=updated['world_minute'], applied_events=resolved_events,
                              narration=narration, reason='Turn recorded')
        if reason:
            result['reason'] = reason
        check('result', result)
        con.execute('INSERT INTO requests VALUES (?,?,?)', (proposal['request_id'], digest, check_json(result)))
        if not background:
            self._record_narration(con, result)
        return result

    def result(self, request_id):
        with self.transaction() as con:
            row = con.execute('SELECT result FROM requests WHERE request_id=?', (request_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def public_consequences(self,request_id):
        from .consequences import public_projection
        result=self.result(request_id)
        return public_projection(result['applied_events']) if result and result['status']=='recorded' else []

    def journal(self, recipient='player'):
        with self.transaction() as con:
            events = [json.loads(row[0]) for row in con.execute('SELECT payload FROM events ORDER BY rowid')]
        return [e for e in events if recipient in e['audience']]

    def recover_tasks(self):
        # Do not automatically resume a call that may have been billed.
        with self.transaction() as con:
            con.execute(("UPDATE tasks SET status='cancelled',reason='Process interrupted; re-evaluation needed' "
                         "WHERE status IN ('pending','in_progress')"))
            con.execute('UPDATE cycles SET active=0')

    def export_resume(self):
        """Derived summary, written under the same lock as the other operations."""
        target=self.path.with_name('RESUME.md')
        with self.transaction() as con:
            state=self._state(con)
            session=self._session(con)
            text=(f"# Campaign resume {state['campaign_id']}\n\n"
                  f"Saved state: version {state['state_version']}.\n\n"
                  f"Fictional time: {state['world_minute']} minutes.\n\n"
                  f"Player position: {state['positions'].get('player','unknown')}.\n\n"
                  f"Canon: revision {state['versions']['canon_revision']}.\n\n"
                  f"The {self.path.name} database is authoritative. This summary is derived from the save.\n"
                  f"Phase: {session['phase']}.\n\n"
                  f"Last text shown:\n\n{session['scene'] or 'No scene recorded.'}\n\n")
            if session['pending_action']:
                text += f"Action to resolve: {session['pending_action']['text']}\n\n"
            text += ("Read the JSON resume to retrieve the latest exchanges. Do not reinitialize the "
                     "campaign.\n")
            temporary=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
            temporary.write_text(text,encoding='utf-8')
            temporary.replace(target)
        return target
