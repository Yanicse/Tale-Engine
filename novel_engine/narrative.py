"""Loop: input, periodic preparation, narration, validation and saving."""
from contextlib import contextmanager, nullcontext
from copy import deepcopy
import json
import os
from pathlib import Path
import time
import uuid
import hashlib

from .adapters import ModelUnavailable
from . import attempt_archives as archives
from .contracts import ASSETS, ROOT, check, check_json
from .memory import select_facts
from .magic import cost_table
from .instructions import CHRONICLE_REPORTS

# Ceiling raised from 192 to 256 KiB on 17 October 2026: the useful context reached 98%.
CONTEXT_BUDGET_BYTES=262144
# Beyond this share of the ceiling, the JSON output flags that a refusal is approaching.
CONTEXT_ALERT_RATIO=0.85


def project_packet(packet):
    """Projection without touching the save; omitted elements can be recalled locally."""
    from .memory import memory_terms
    from .targeted_context import bound_powers
    packet=deepcopy(packet)
    packet.pop('possessions',None)  # Same inventory, purse and projects in the state.
    state=packet['current_state']
    action_terms=memory_terms(packet.get('player_input',packet.get('action_to_resolve','')))
    omitted={};bound=bound_powers(packet)
    for group in ('inventory','projects','relations','powers'):
        values=state.get(group,{})
        if len(values)<=40:continue
        selected={};index=[]
        documents={key:memory_terms(key+' '+check_json(value)) for key,value in values.items()}
        specific={term for term in action_terms if sum(term in words for words in documents.values())<=max(3,len(values)//10)}
        recovery=packet.get('memory_recovery',{}).get('query','')
        requested=recovery.split(':',1)[1].strip() if ':' in recovery else None
        for key,value in values.items():
            terms=documents[key]
            # The targeted projection handles the bound sheets and those declared always complete.
            # Removing them here would lose their summaries and aliases at the 40-power threshold.
            kept = group == 'powers' and key in bound
            if kept or terms & specific or key==requested or key.casefold() in packet.get('player_input','').casefold() or value.get('equipped'):
                selected[key]=value
            else:
                index.append(key)
        # A small stable slice helps general actions without drowning the prompt.
        for key in list(values)[:12]:
            selected[key]=values[key]
            if key in index:index.remove(key)
        state[group]=selected
        if index:omitted[group]=index
    if omitted:
        packet['omitted_entities_index']=omitted
        packet['entity_reminder']=('The omitted identifiers still exist in SQLite. memory_request.query can ask for '
                                  'object:<id>, project:<id>, relation:<id> or power:<id> for their exact sheet. No '
                                  'absence means non-existence.')
    return packet


def prompt_metrics(instructions,documents,packet,separators,serialize=check_json):
    blocks={'instructions':instructions,'documents':serialize(documents),
        **{key:serialize(value) for key,value in packet.items()},'separators':separators}
    metrics={key:{'characters':len(value),'utf8_bytes':len(value.encode('utf-8'))}
            for key,value in blocks.items()}
    metrics['structure_json']={'characters':len(serialize(packet))-sum(len(serialize(v)) for v in packet.values()),
        'utf8_bytes':len(serialize(packet).encode('utf-8'))-sum(len(serialize(v).encode('utf-8')) for v in packet.values())}
    return metrics


def packet_references(packet):
    rules=packet.get('campaign_rules',{})
    references=set(rules.get('seed_references',[]))|set(rules.get('rule_ids',[]))
    def visit(value):
        if isinstance(value,list):
            for item in value:visit(item)
        elif isinstance(value,dict):
            for key,item in value.items():
                if key in ('allowed_references','memory_references'):continue
                if key in ('reference','current_action_reference') and isinstance(item,str):references.add(item)
                elif key=='references' and isinstance(item,list):references.update(item)
                else:visit(item)
    visit(packet)
    references.update('exchange:'+str(item['seq']) for key in ('recent_exchanges','recalled_exchanges')
                      for item in packet.get(key,[]) if item['role']=='narrator')
    return references


@contextmanager
def narrative_lock(campaign):
    """A single narrator per campaign; the lock is released after a crash."""
    path = campaign.path.with_suffix('.narration.lock')
    with path.open('a+b') as handle:
        handle.seek(0,2)
        if handle.tell()==0:
            handle.write(b'0');handle.flush()
        handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ModelUnavailable('Another narrator is already handling this campaign.') from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name=='nt':
                msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                fcntl.flock(handle.fileno(),fcntl.LOCK_UN)


class NarrativeLoop:
    def __init__(self, campaign, narrator, word_target=180, planner=None, context_budget_bytes=CONTEXT_BUDGET_BYTES, documents_root=None):
        if type(word_target) is not int or not 40 <= word_target <= 1000:
            raise ValueError('Narrative length between 40 and 1000 words expected')
        self.campaign, self.narrator, self.word_target = campaign, narrator, word_target
        self.documents = None
        self.last_metrics = {}
        self.planner = planner
        if type(context_budget_bytes) is not int or context_budget_bytes<=0:raise ValueError('Budget UTF-8 positive required.')
        self.context_budget_bytes=context_budget_bytes
        self.documents_root=Path(documents_root) if documents_root is not None else ROOT
        # Shipped rules come from the package unless a test or caller supplies its own root.
        self.assets_root=self.documents_root if documents_root is not None else ASSETS
        self.document_signature=None

    def _documents(self):
        config = self.campaign.config()
        paths = ['docs/engine/common_rules.md','docs/engine/agents/narrator.md']
        if config.get('private_preparation'):
            paths.append(config['private_preparation'])
        from .canon_selection import public_reference_path
        public_path=public_reference_path(config)
        if public_path is not None:paths.append(str(public_path.relative_to(ROOT)))
        if config.get('character_sheet'):
            paths.append(config['character_sheet'])
        if config.get('possession_rules'):
            paths.append('docs/engine/POSSESSIONS.md')
        roots=[self.assets_root if relative.startswith(('docs/engine/','adaptation/')) else self.documents_root for relative in paths]
        files=[(root/relative).resolve() for root,relative in zip(roots,paths)]
        if any(not path.is_relative_to(root.resolve()) for path,root in zip(files,roots)):
            raise ValueError('Preparation outside the project refused')
        signature=tuple((str(path),path.stat().st_mtime_ns,path.stat().st_size) for path in files)
        if self.documents is None or self.document_signature!=signature:
            self.documents = []
            for relative,path in zip(paths,files):
                self.documents.append({'source':relative,'text':path.read_text(encoding='utf-8')})
            self.document_signature=signature
        return self.documents

    def build_prompt(self, prepared, compact=True):
        packet, state, facts, history = self._prompt_packet(prepared, compact)
        self._prompt_references(prepared, packet, state, facts, history)
        instructions = self._full_instructions(prepared)
        documents=self._documents()
        from . import targeted_context
        targeted = compact and targeted_context.enabled()
        if compact:
            from .narrative_preparation import (select_modules, build_instructions,
                                               situation_sheet, select_documents)
            modules = select_modules(packet)
            if targeted:
                packet = targeted_context.project(packet)
            packet['situation_sheet'] = situation_sheet(packet)
            packet['rule_modules'] = modules
            instructions = build_instructions(packet, modules, self.word_target)
            documents = select_documents(documents, modules, packet)
            self.last_metrics['rule_modules'] = modules
        from .instructions import WORLD_UNKNOWNS, RELATIONS, LOCAL_SHEETS
        instructions+=WORLD_UNKNOWNS+RELATIONS+LOCAL_SHEETS
        if packet.get('npc_sheets'):
            from .npc_sheets import INSTRUCTIONS as NPC_INSTRUCTIONS
            instructions += NPC_INSTRUCTIONS
        if packet.get('place_reminders'):
            from .place_reminders import INSTRUCTIONS as REMINDER_INSTRUCTIONS
            instructions += REMINDER_INSTRUCTIONS
        if prepared['campaign_rules'].get('possession_rules'):
            from .instructions import TRANSFERS
            instructions+=TRANSFERS
        if prepared['campaign_rules'].get('creation_format')=='1':
            from .creation_policy import TRANSPORT
            instructions+=TRANSPORT
        serialize = targeted_context.serialize if targeted else check_json
        if targeted:
            documents = targeted_context.deduplicate_documents(documents, instructions)
        return self._compose_prompt(instructions, documents, packet, compact, serialize, targeted)

    def _prompt_packet(self, prepared, compact):
        """State, memory, world and abilities of the turn; optional compact projection."""
        if prepared.get('world_agenda',{}).get('technical_diagnostic'):
            raise ModelUnavailable('Agenda group impossible to provide in full: '+
                                   check_json(prepared['world_agenda']['technical_diagnostic']))
        state = deepcopy(prepared['state'])
        event_ids = set(state.pop('applied_event_ids'))
        context = prepared['context']
        all_facts = [item for item in context['information'] if item['id'] in event_ids]
        canon_facts = [item for item in context['information'] if item['origin']=='canon']
        resumed = prepared['resume']
        history = resumed['recent_exchanges']
        scene = resumed['session']['scene']
        memory = prepared.get('narrative_memory_context')
        context_text = ' '.join(item['content'] for item in history[-6:])
        facts = (memory['facts'] if memory is not None else
                 select_facts(all_facts,context['player_action'],context_text=context_text)) + canon_facts
        packet = {'current_state':state,'player_input':context['player_action'],
                  'recent_exchanges':history,'extra_facts':facts,
                  'unknowns':list(context['unknowns']),'campaign_rules':prepared['campaign_rules']}
        from .possessions import packet as possessions_packet
        packet['possessions'] = possessions_packet(state, {'possession_rules': prepared['campaign_rules'].get('possession_rules', {})}, 'narrator')
        with self.campaign.transaction() as con:
            if con.execute("SELECT 1 FROM sqlite_master WHERE name='memory_recovery'").fetchone():
                recovery = con.execute('SELECT status,query,reason FROM memory_recovery WHERE request_id=?',
                                       (prepared['model_proposal']['request_id'],)).fetchone()
                if recovery:
                    packet['memory_recovery'] = dict(recovery)
        if memory is not None:
            packet['recalled_exchanges'] = memory['exchanges']
            packet['files'] = memory.get('files',[])
            packet['memory_proofs'] = [{key:value for key,value in item.items() if key!='text'}
                                        for item in memory.get('proofs',[])]
            packet['search_memory'] = {k:v for k,v in memory.get('search',{}).items() if k!='diagnostic'}
        self._memory_selection = deepcopy((memory or {}).get('search',{}).get('diagnostic',{}))
        self._npc_selection = deepcopy(prepared.get('npc_diagnostic',{}))
        self._place_selection = deepcopy(prepared.get('place_reminders_diagnostic',{}))
        packet['world_agenda']=prepared.get('world_agenda',{})
        packet['world_planning']=prepared.get('world_planning',{})
        packet['campaign_creations']=prepared.get('campaign_creations',[])
        if prepared.get('npc_sheets'):
            packet['npc_sheets'] = deepcopy(prepared['npc_sheets'])
        if prepared.get('place_reminders'):
            packet['place_reminders'] = deepcopy(prepared['place_reminders'])
        from .scene_pacing import packet as pacing_packet
        with self.campaign.transaction() as con:
            pacing = pacing_packet(con, json.loads(con.execute('SELECT config FROM campaign WHERE id=1').fetchone()[0]))
        if pacing:
            packet['scene_pacing'] = pacing
        if getattr(self,'_correction_code',None):
            packet['correction_technique']={'code':self._correction_code,
                'instruction':("The previous receipt was refused. Fix the structure and the references according to the "
                            "provided contract; preserve the player's choices and the validations.")}
        if getattr(self, '_agenda_retry_correction', None):
            packet['agenda_command_correction'] = deepcopy(self._agenda_retry_correction)
        if prepared.get('documentary_canon') is not None:
            packet['documentary_canon']=prepared['documentary_canon']
        if prepared['campaign_rules'].get('spell_cost_rules'):
            packet['computed_spell_costs']=cost_table(state['resources']['player_hp_max'],
                prepared['campaign_rules']['spell_cost_rules'])
        if prepared['campaign_rules'].get('character_rules'):
            from .character import ability_context
            packet['character_abilities']=ability_context(state,
                prepared['campaign_rules']['character_rules'],
                prepared['campaign_rules'].get('spell_cost_rules'))
        previous_ids = (resumed['session']['pending_action'] or {}).get('previous_request_ids',[])
        if previous_ids:
            previous_result = self.campaign.result(previous_ids[-1])
            if previous_result and previous_result['status']=='rejected':
                packet['reevaluation'] = {'rejection_reason':previous_result['reason'],
                    'instruction':('Fix the rejected proposal starting from the current state; none of its consequences was '
                                'applied.')}
        total_facts = memory['total_facts'] if memory is not None else len(all_facts)
        if len(facts)<total_facts+len(canon_facts):
            packet['unknowns'].append(("Log selected locally from the complete save, with the action and the recent exchanges. "
                                        "Older facts stay saved; their absence from the context does not mean they do not exist. "
                                        "The recalled exchanges reproduce the saved texts: a player's intention does not prove "
                                        "its realization. Do not invent a missing historical fact nor ask the player to restore "
                                        "the program's archives. Tell an outcome unknown to the player apart from an ambiguous "
                                        "action needing their clarification."))
        if not any(item['role']=='narrator' and item['content']==scene for item in history):
            packet['last_recorded_text']=scene
        if compact:
            packet=project_packet(packet)
            state=packet['current_state']
        return packet, state, facts, history

    def _prompt_references(self, prepared, packet, state, facts, history):
        """Authorized references and exact memory sources, added to the packet."""
        reference_ids = set(prepared['campaign_rules'].get('seed_references',[]))
        reference_ids.update(prepared['campaign_rules'].get('rule_ids',[]))
        reference_ids.update(ref for currency in prepared['campaign_rules'].get('possession_rules', {}).get('currencies', {}).values()
                             for ref in currency.get('references', []))
        reference_ids.update(ref for group in ('inventory','effects','relations','powers','commitments','projects')
                             for item in state.get(group, {}).values() for ref in item.get('references',[]))
        reference_ids.update(ref for item in facts for ref in item['references'])
        reference_ids.update(ref for item in packet['campaign_creations'] for ref in item['references'])
        reference_ids.update(ref for item in packet.get('npc_sheets',[]) for ref in item['references'])
        reference_ids.update(ref for item in packet.get('place_reminders',[]) for ref in item['references'])
        reference_ids.update('exchange:'+str(item['seq']) for item in packet.get('recalled_exchanges',[]) if item['role']=='narrator')
        reference_ids.update('exchange:'+str(item['seq']) for item in history if item['role']=='narrator')
        reference_ids.update(item['reference'] for item in packet.get('memory_proofs',[]))
        reference_ids.update(item['reference'] for item in packet['world_agenda'].get('events',[]))
        reference_ids.update(ref for item in packet['world_agenda'].get('events',[]) for ref in item['references'])
        reference_ids.update(item['reference'] for item in packet['world_planning'].get('situations',[]))
        reference_ids.update(item['reference'] for item in packet['world_planning'].get('private_world_facts',[]))
        reference_ids.update(item['reference'] for item in packet.get('documentary_canon',{}).get('facts',[]))
        reference_ids.add('action:'+prepared['model_proposal']['request_id'])
        packet['allowed_references']=sorted(reference_ids)
        # A cause can be a rule; a file requires an exact archive.
        with self.campaign.transaction() as con:
            packet['memory_references'] = []
            if reference_ids and con.execute("SELECT 1 FROM sqlite_master WHERE name='memory_sources'").fetchone():
                refs_json=check_json(sorted(reference_ids))
                packet['memory_references'] = [r[0] for r in con.execute(
                    'SELECT reference FROM memory_sources WHERE reference IN (SELECT value FROM json_each(?)) ORDER BY reference',
                    (refs_json,))]
        packet['current_action_reference']='action:'+prepared['model_proposal']['request_id']

    def _full_instructions(self, prepared):
        """Complete instructions: every rule module, as opposed to the compact selection."""
        from .narrative_preparation import ALL_MODULES, build_instructions
        return build_instructions({'campaign_rules': prepared['campaign_rules']}, ALL_MODULES, self.word_target)

    def _compose_prompt(self, instructions, documents, packet, compact, serialize, targeted):
        """Final assembly within the UTF-8 budget, optional blocks omitted before any refusal."""
        # Reduce the optional blocks before any mandatory constraint.
        omitted_blocks=[]
        omitted_entities = deepcopy(packet.get('omitted_entities_index', {}))
        def compose():
            # Measure the references actually sent, including after each omission.
            if compact:
                packet['allowed_references']=sorted(packet_references(packet))
                packet['memory_references']=[ref for ref in packet['memory_references']
                                             if ref in packet['allowed_references']]
            return instructions+('PREPARATION DOCUMENTS\n')+serialize(documents)+'\nCURRENT TURN\n'+serialize(packet)
        prompt=compose()
        if compact and len(prompt.encode('utf-8'))>self.context_budget_bytes:
            for key in ('world_planning','files','npc_sheets','place_reminders','omitted_entities_index'):
                if key in packet:
                    omitted_blocks.append(key)
                    packet.pop(key)
                    packet['omitted_blocks']=omitted_blocks
                    prompt=compose()
                    if len(prompt.encode('utf-8'))<=self.context_budget_bytes:break
        self.last_metrics['context_blocks']=prompt_metrics(instructions,documents,packet,('PREPARATION DOCUMENTS\n\n'
                                                                                          'CURRENT TURN\n'),serialize)
        self.last_metrics['targeted_context'] = targeted
        self.last_metrics['prompt_characters']=len(prompt)
        self.last_metrics['prompt_bytes']=len(prompt.encode('utf-8'))
        self.last_metrics['context_budget_bytes']=self.context_budget_bytes
        self.last_metrics['budget_ratio']=round(self.last_metrics['prompt_bytes']/self.context_budget_bytes,3)
        self.last_metrics['alert_budget']=self.last_metrics['budget_ratio']>CONTEXT_ALERT_RATIO
        self.last_metrics['document_signature']=hashlib.sha256(check_json(self.document_signature).encode()).hexdigest()
        from .context_diagnostic import build as context_diagnostic
        self.last_metrics['context_diagnostic'] = context_diagnostic(
            packet, getattr(self,'_memory_selection',{}), getattr(self,'_npc_selection',{}),
            omitted_blocks, omitted_entities)
        self.last_metrics['context_diagnostic']['place_reminders'] = deepcopy(getattr(self,'_place_selection',{}))
        transmitted_reminders = {r['id'] for r in packet.get('place_reminders',[])}
        for reminder in self.last_metrics['context_diagnostic']['place_reminders'].get('reminders',[]):
            reminder['sent'] = reminder['id'] in transmitted_reminders
            if reminder['status']=='kept' and not reminder['sent']:
                reminder['final_reason'] = 'prompt_budget'
        if compact and self.last_metrics['prompt_bytes']>self.context_budget_bytes:
            raise ModelUnavailable('Mandatory core beyond the UTF-8 budget: '+str(self.last_metrics['prompt_bytes'])+' > '+str(self.context_budget_bytes)+'. No rule truncated, no generation.')
        return prompt

    def _attempts(self, con):
        con.execute('''CREATE TABLE IF NOT EXISTS narrative_attempts (
            request_id TEXT PRIMARY KEY, status TEXT NOT NULL, proposal TEXT,
            metrics TEXT NOT NULL, reason TEXT NOT NULL)''')

    def _save_attempt(self, request_id, status, proposal=None, reason=''):
        with self.campaign.transaction() as con:
            self._attempts(con)
            if reason and status=='failure':
                self.last_metrics['validation_error_sha256']=hashlib.sha256(reason.encode('utf-8')).hexdigest()
            old=con.execute('SELECT * FROM narrative_attempts WHERE request_id=?',(request_id,)).fetchone()
            if old is not None:
                # Always start again from the complete receipt, even if it was archived.
                old={**dict(old),'proposal':archives.restore(con,request_id,old['proposal'])}
            if status=='in_progress' and old:
                con.execute('''CREATE TABLE IF NOT EXISTS narrative_attempt_history (
                    id INTEGER PRIMARY KEY,request_id TEXT,status TEXT,proposal TEXT,metrics TEXT,reason TEXT)''')
                con.execute('INSERT INTO narrative_attempt_history(request_id,status,proposal,metrics,reason) VALUES (?,?,?,?,?)',
                            (old['request_id'],old['status'],old['proposal'],old['metrics'],old['reason']))
                if archives.enabled(con):
                    con.execute(f'DELETE FROM {archives.TABLE} WHERE request_id=?',(request_id,))
                con.execute('DELETE FROM narrative_attempts WHERE request_id=?',(request_id,))
                old=None
            if proposal is not None and old and old['proposal']:
                proposal={**json.loads(old['proposal']),**proposal}
            if proposal is not None and any(key in proposal for key in ('received','proposal')):
                proposal={**proposal,'receipt_runtime':proposal.get('receipt_runtime',{
                    'recovery_policy':'bounded_v1' if getattr(self,'_automatic',False) else 'history',
                    'creation_format':proposal.get('prepared',{}).get('campaign_rules',{}).get('creation_format')})}
            stored=check_json(proposal) if proposal is not None else None
            if stored is not None and archives.enabled(con):
                if status in archives.TERMINAL_STATUSES:
                    stored=archives.archive(con,request_id,stored)
                else:
                    con.execute(f'DELETE FROM {archives.TABLE} WHERE request_id=?',(request_id,))
            con.execute('''INSERT INTO narrative_attempts VALUES (?,?,?,?,?)
                ON CONFLICT(request_id) DO UPDATE SET status=excluded.status,
                proposal=COALESCE(excluded.proposal,narrative_attempts.proposal),
                metrics=excluded.metrics,reason=excluded.reason''',
                (request_id,status,stored,
                 check_json(self.last_metrics),reason))

    def _previous_attempt(self, request_id):
        with self.campaign.transaction() as con:
            self._attempts(con)
            row = con.execute('SELECT * FROM narrative_attempts WHERE request_id=?',(request_id,)).fetchone()
            return {**dict(row),'proposal':archives.restore(con,request_id,row['proposal'])} if row else None

    def _received_response(self, request_id, response, prompt):
        # Escaped ASCII: keep even a malformed output without writing U+2014.
        packet=json.loads(prompt.rsplit('\nCURRENT TURN\n',1)[1])
        decoded=response
        if isinstance(response,str):
            try:decoded=json.loads(response)
            except ValueError:decoded=None
        missing=[]
        if isinstance(decoded,dict) and isinstance(decoded.get('events'),list):
            known=set(packet['allowed_references'])|{'current_action'}
            known.update('current_event:'+str(i) for i in range(64))
            for index,event in enumerate(decoded['events']):
                if isinstance(event,dict) and isinstance(event.get('causes'),list):
                    missing.extend({'event_index':index,'reference':ref} for ref in event['causes']
                                   if isinstance(ref,str) and ref not in known)
        payload=json.dumps({'response':response,'allowed_references':packet['allowed_references'],
                            'missing_causes':missing,'state_version':packet['current_state']['state_version']},
                            ensure_ascii=True,allow_nan=False)
        with self.campaign.transaction() as con:
            con.execute('''CREATE TABLE IF NOT EXISTS narrative_diagnostics (
                id INTEGER PRIMARY KEY,request_id TEXT NOT NULL,payload TEXT NOT NULL,error TEXT NOT NULL)''')
            con.execute('CREATE INDEX IF NOT EXISTS narrative_diagnostics_request ON narrative_diagnostics(request_id,id)')
            cursor=con.execute('INSERT INTO narrative_diagnostics(request_id,payload,error) VALUES (?,?,?)',
                               (request_id,payload,''))
            return cursor.lastrowid

    def _diagnostic_error(self, identifier, reason):
        with self.campaign.transaction() as con:
            con.execute('UPDATE narrative_diagnostics SET error=? WHERE id=?',(reason,identifier))

    def _diagnostic_corrections(self, identifier):
        corrections={key:self.last_metrics[key] for key in
            ('agenda_audience_corrections','agenda_date_corrections','continuation_corrections') if self.last_metrics.get(key)}
        if not corrections:return
        with self.campaign.transaction() as con:
            row=con.execute('SELECT payload FROM narrative_diagnostics WHERE id=?',(identifier,)).fetchone()
            payload=json.loads(row['payload'])
            payload.update(corrections)
            con.execute('UPDATE narrative_diagnostics SET payload=? WHERE id=?',
                        (json.dumps(payload,ensure_ascii=True,allow_nan=False),identifier))

    def _proposal(self, prepared, response, packet=None):
        from .narration_codex import normalize_response
        response = normalize_response(response)
        self._creation_updates = response.pop('creation_updates', [])
        response.pop('creation_schema_version', None)
        if self._creation_updates and prepared['campaign_rules'].get('creation_format')!='1':
            raise ValueError('Creations format not installed.')
        command_protocol = response['narrative_schema_version'] in ('1.2','1.3')
        if prepared['campaign_rules'].get('mechanics') and response['narrative_schema_version']!='1.3':
            raise ValueError('Mechanics campaign active: new 1.3 answer required, historical receipt kept.')
        if command_protocol:
            from .agenda_protocol import materialize
            response['agenda_updates'] = materialize(response['agenda_updates'], prepared['world_agenda'])
        if response['memory_request']:
            raise ValueError('A technical request cannot be applied as a turn.')
        updates = response.pop('memory_updates')
        agenda_updates=response.pop('agenda_updates')
        continuation=response.pop('continuation')
        rolls=response.pop('roll_checks')
        response.pop('memory_request')
        response.pop('narrative_schema_version')
        proposal = deepcopy(prepared['model_proposal'])
        proposal.update(deepcopy(response))
        # The model can choose neither the campaign, nor the identifier, nor the version.
        for key in ('schema_version','campaign_id','request_id','expected_state_version'):
            proposal[key] = prepared['model_proposal'][key]
        for index,event in enumerate(proposal['events']):
            event['event_id'] = uuid.uuid5(uuid.NAMESPACE_URL,proposal['request_id']+':'+str(index)).hex
            for condition in event['conditions']:
                condition['equals'] = json.loads(condition.pop('equals_json'))
            for change in event['changes']:
                raw=change.pop('value_json')
                try:change['value'] = json.loads(raw)
                except ValueError:
                    # Raw text left outside JSON quotes: unambiguous only outside an object, list or open string.
                    if not isinstance(raw,str) or not raw.strip() or raw.lstrip()[0] in '{["':raise
                    change['value'] = raw
                    self.last_metrics['value_json_raw_text']=self.last_metrics.get('value_json_raw_text',0)+1
        for index,event in enumerate(proposal['events']):
            for pos,cause in enumerate(event['causes']):
                if cause=='current_action':
                    event['causes'][pos]='action:'+proposal['request_id']
                    continue
                if cause.startswith('current_event:'):
                    earlier=int(cause.split(':')[1])
                    if not 0<=earlier<index:raise ValueError('Turn cause in the future or circular.')
                    event['causes'][pos]=proposal['events'][earlier]['event_id']
            for operation in event.get('mechanical_operations',[]):
                source=operation['source_reference']
                if source=='current_action':source='action:'+proposal['request_id']
                elif source.startswith('current_event:'):
                    earlier=int(source.split(':')[1])
                    if not 0<=earlier<index:raise ValueError('Future mechanical cause.')
                    source=proposal['events'][earlier]['event_id']
                operation['source_reference']=source
            for claim in event.get('public_consequences',[]):
                source=claim['source_reference']
                if source=='current_action':source='action:'+proposal['request_id']
                elif source.startswith('current_event:'):
                    earlier=int(source.split(':')[1])
                    if not 0<=earlier<=index:raise ValueError('Public consequence from a future source.')
                    source=proposal['events'][earlier]['event_id']
                claim['source_reference']=source
            def sourced(value):
                if not isinstance(value, dict) or 'references' not in value:
                    return value
                for pos, ref in enumerate(value['references']):
                    if ref == 'current_action':
                        value['references'][pos] = 'action:' + proposal['request_id']
                    elif ref.startswith('current_event:'):
                        current = int(ref.split(':')[1])
                        if not 0 <= current <= index:
                            raise ValueError('Possession source in the future')
                        value['references'][pos] = proposal['events'][current]['event_id']
                return value
            for change in event['changes']:
                change['value'] = sourced(change['value'])
            for operation in event['asset_operations']:
                for output in operation['outputs']:
                    output['item_json'] = check_json(sourced(json.loads(output['item_json'])))
        if not proposal['blocking_questions'] and not proposal['narration'].strip():
            raise ValueError('Empty narration refused')
        # Same list as the remote schema and the prompt, no implicit widening.
        if packet is None:
            packet=json.loads(self.build_prompt(prepared).rsplit('\nCURRENT TURN\n',1)[1])
        self._location_reminders = deepcopy(packet.get('place_reminders',[]))
        allowed=set(packet['allowed_references'])
        agenda_packet=prepared.get('world_agenda',{'events':[],'d100_rolls':{}})
        for event in proposal['events']:
            missing=[cause for cause in event['causes'] if cause not in allowed]
            if missing:
                raise ValueError('Cause without an authorized reference refused: '+', '.join(missing[:4]))
            allowed.add(event['event_id'])
            for change in event['changes']:
                if change['path'].startswith(('/inventory/', '/projects/')) and isinstance(change['value'], dict):
                    if set(change['value'].get('references', [])) - allowed:
                        raise ValueError('Possession or project source not provided')
            for operation in event['asset_operations']:
                for output in operation['outputs']:
                    if set(json.loads(output['item_json']).get('references', [])) - allowed:
                        raise ValueError('Possession source not provided')
        from .indexed_memory import MemoryIndex
        with self.campaign.transaction() as con:
            from .creations import CreationRegistry
            for update in self._creation_updates:
                update['references'] = [proposal['events'][int(ref.split(':')[1])]['event_id']
                    if ref.startswith('current_event:') and 0<=int(ref.split(':')[1])<len(proposal['events'])
                    else ref for ref in update['references']]
            CreationRegistry(con).validate(self._creation_updates, proposal, allowed)
            MemoryIndex(con).validate_updates(updates,allowed,proposal)
            from .agenda import Agenda
            for update in agenda_updates:
                for condition in update['conditions']:
                    condition['equals']=json.loads(condition.pop('equals_json'))
                refs=[]
                for ref in update['references']:
                    if ref=='current_action':ref='action:'+proposal['request_id']
                    if ref.startswith('current_event:'):
                        index=int(ref.split(':')[1])
                        if not 0<=index<len(proposal['events']):raise ValueError('Turn source missing.')
                        ref=proposal['events'][index]['event_id']
                    refs.append(ref)
                update['references']=refs
            agenda_allowed=allowed|{e['event_id'] for e in proposal['events']}
            agenda_updates,corrections=Agenda(con).normalize_audiences(proposal,agenda_updates, commands=command_protocol)
            self.last_metrics['agenda_audience_corrections']=corrections
            # A "suspended" goal without a perceivable event is not an interruption: the rest
            # of a routine or an ellipsis is told in the narration, hence continuation=null.
            continuation_corrections=[]
            if continuation is not None and continuation.get('status')=='suspended' and continuation.get('event_index') is None:
                continuation_corrections.append({'reason':'suspended_without_perceivable_event','continuation':continuation})
                continuation=None
            self.last_metrics['continuation_corrections']=continuation_corrections
            date_corrections=[]
            if not command_protocol:
                agenda_updates,date_corrections=Agenda(con).normalize_resolution_dates(proposal,agenda_updates,prepared['state'])
            self.last_metrics['agenda_date_corrections']=date_corrections
            Agenda(con).validate(proposal,agenda_updates,continuation,rolls,
                                 agenda_allowed,prepared['state'],agenda_packet)
            from .scene_pacing import validate as validate_pacing
            validate_pacing(packet.get('scene_pacing', []), proposal, agenda_updates)
        self._memory_updates = updates
        self._agenda_changes={'updates':agenda_updates,'continuation':continuation,
                              'roll_checks':rolls}
        return check('proposal',proposal)

    def run_turn(self, text=None, resume=False, reevaluate=False, retry=False, query=''):
        from .bounded_resume import POLICY, classify, transition, stopped
        automatic = self.campaign.config().get('recovery_policy') == POLICY
        with narrative_lock(self.campaign):
            self._automatic = automatic
            self._correction_code = None
            if not automatic or not (text is not None or resume or reevaluate):
                return self._run_turn_once(text,resume,reevaluate,retry,query)
            recoveries, calls, planning_calls = 0, 0, 0
            started = time.monotonic()
            for step in range(5):
                self._phase = 'preparation'
                try:
                    result = self._run_turn_once(text,resume,reevaluate,retry,query)
                except KeyboardInterrupt:
                    # The receipt in progress is kept; no second generation.
                    raise
                except (ModelUnavailable, ValueError, TypeError, KeyError, OSError) as exc:
                    pending = self.campaign.resume()['session']['pending_action']
                    if not pending: raise
                    rid = pending['request_id']
                    code, can_retry = classify(exc,self.narrator,self._phase)
                    from .recovery import RecoveryBudget
                    with self.campaign.transaction() as con:
                        remaining = not RecoveryBudget(con,rid).generation_exhausted()
                    again = can_retry and remaining and recoveries < 1
                    transition(self.campaign,rid,self._phase,code,again)
                    calls += self.last_metrics.get('model_calls',0)
                    planning_calls += self.last_metrics.get('planner',{}).get('model_calls',0)
                    if again:
                        recoveries += 1
                        self._correction_code = code
                        text,resume,reevaluate,retry = None,True,False,True
                        continue
                    result = stopped(self.campaign,rid,code,self.last_metrics,self._phase)
                else:
                    calls += self.last_metrics.get('model_calls',0)
                    planning_calls += self.last_metrics.get('planner',{}).get('model_calls',0)
                    if result['status']=='required_memory':
                        rid=result['request_id']
                        status=result.get('search_status')
                        from .recovery import RecoveryBudget
                        with self.campaign.transaction() as con:
                            remaining=not RecoveryBudget(con,rid).generation_exhausted()
                        again=status in ('ready','partial','in_game_verification') and remaining
                        transition(self.campaign,rid,'search','sources_'+str(status),again)
                        if again:
                            recoveries += 1
                            text,resume,reevaluate,retry = None,True,False,False
                            continue
                        result=stopped(self.campaign,rid,'sources_or_budget',self.last_metrics,'search')
                    elif result['status']=='rejected':
                        transition(self.campaign,result['request_id'],'application','refusal_engine',False)
                        result=stopped(self.campaign,result['request_id'],'refusal_engine',self.last_metrics,'application')
                self.last_metrics.update(model_calls=calls, narrative_calls=calls-planning_calls,
                    planning_calls=planning_calls, automatic_recoveries=recoveries,
                    total_seconds=round(time.monotonic()-started,3), recovery_policy=POLICY)
                return result
            pending=self.campaign.resume()['session']['pending_action']
            return stopped(self.campaign,pending['request_id'],'limited_transitions',self.last_metrics)

    def _run_turn_once(self, text=None, resume=False, reevaluate=False, retry=False, query=''):
        started = time.monotonic()
        self.last_metrics = {'model_calls':0}
        with nullcontext():
            saved = self.campaign.resume()
            pending = saved['session']['pending_action']
            if resume and pending is None:
                last_id = saved['session']['last_request_id']
                result = self.campaign.result(last_id) if last_id else None
                if result:
                    self.last_metrics['reused_answer'] = True
                    return {key: value for key, value in result.items() if key != 'applied_events'}
                return {'status':'resume','narration':saved['session']['scene'],'request_id':last_id}
            if text is None and not (resume or reevaluate):
                return {'status':'resume','narration':saved['session']['scene'],
                        'request_id':saved['session']['last_request_id']}
            if pending and not (resume or reevaluate):
                raise ModelUnavailable('An action is already waiting. Use --resume explicitly; no automatic call.')
            if text is not None and pending and text!=pending['text']:
                raise ValueError('Another action is already waiting; resume before a new input')
            if pending and resume:
                previous_attempt=self._previous_attempt(pending['request_id'])
                if previous_attempt and previous_attempt['status']=='required_context':
                    with self.campaign.transaction() as con:
                        recovery=con.execute('SELECT query FROM memory_recovery WHERE request_id=?',
                                             (pending['request_id'],)).fetchone()
                    if recovery is None or (query and query!=recovery['query']):
                        self.campaign.recover_memory(pending['request_id'],
                            query or previous_attempt['reason'])
            prepared = self.campaign.prepare_turn(text,query,reevaluate=reevaluate,narrative_memory=True)
            self.last_metrics['preparation_seconds'] = round(time.monotonic()-started,3)
            if prepared['status']!='ready':
                if prepared['status']=='rejected_action':
                    reason = prepared['previous_result']['reason']
                    raise ModelUnavailable('Action rejected: '+reason+'. Use --reevaluate for a new resolution.')
                raise ModelUnavailable('No pending action. Enter a new action.')
            template = prepared['model_proposal']
            request_id = template['request_id']
            attempt = self._previous_attempt(request_id)
            self._agenda_retry_correction = None
            if retry and attempt and attempt['status'] == 'failure' and attempt['proposal']:
                from .agenda_protocol import METADATA_ERROR, retry_metadata_correction
                if METADATA_ERROR in (attempt['reason'] or ''):
                    self._agenda_retry_correction = retry_metadata_correction(json.loads(attempt['proposal']))
            proposal = None
            received = None
            received_packet = None
            received_prompt = None
            expected_versions=prepared['state']['versions']
            if attempt and attempt['status']=='required_context' and resume:
                with self.campaign.transaction() as con:
                    row = con.execute('SELECT status FROM memory_recovery WHERE request_id=?',(request_id,)).fetchone()
                if row and row['status'] not in ('ready', 'partial', 'in_game_verification'):
                    from .recovery import RecoveryBudget
                    with self.campaign.transaction() as con:
                        return RecoveryBudget(con,request_id).blocked('Search without progress.')
            elif attempt and attempt['proposal'] and not retry:
                stored = json.loads(attempt['proposal'])
                if stored.get('expected_versions',expected_versions)!=expected_versions:
                    if not retry:
                        raise ModelUnavailable('Canon or adaptation modified after preparation; use --resume --retry explicitly.')
                else:
                    if 'proposal' in stored:
                        proposal = stored['proposal']
                    elif 'received' in stored:
                        received = stored['received']
                        received_packet = stored['packet']
                        received_prompt = stored.get('prompt')
                        prepared = stored['prepared']
                    else:
                        proposal = stored.get('proposal',stored)
                    self._memory_updates = stored.get('memory_updates',[])
                    self._creation_updates = stored.get('creation_updates',[])
                    self._location_reminders = stored.get('location_reminders',[])
                    self._agenda_changes=stored.get('agenda_changes',{'updates':[],'continuation':None,'roll_checks':[]})
                    self.last_metrics['agenda_audience_corrections']=json.loads(attempt['metrics']).get('agenda_audience_corrections',[])
                    self.last_metrics['agenda_date_corrections']=json.loads(attempt['metrics']).get('agenda_date_corrections',[])
                    self.last_metrics['reused_answer'] = True
            elif attempt and not retry and attempt['status']!='preparation_refused':
                raise ModelUnavailable('A generation was already attempted. Use --resume --retry to allow a new call.')
            if proposal is None:
                if received is None:
                    from .recovery import RecoveryBudget
                    with self.campaign.transaction() as con:
                        budget=RecoveryBudget(con,request_id)
                        if budget.generation_exhausted():
                            return budget.blocked('Generation budget reached for this action.')
                if prepared.get('world_agenda',{}).get('technical_diagnostic') and received is None:
                    raise ModelUnavailable('Agenda group too dense before generation: '+
                                           check_json(prepared['world_agenda']['technical_diagnostic']))
                if self.planner is not None and received is None:
                    from .planner import review_world
                    planning=review_world(self.campaign,self.planner,prepared,retry=retry and not self._automatic)
                    self.last_metrics['planner']=planning
                    self.last_metrics['model_calls']=planning['model_calls']
                    # The preparation must see the deadlines created before resolving a wait.
                    prepared=self.campaign.prepare_turn(query=query,narrative_memory=True)
                diagnostic_id=None
                prompt=None
                response_received=False
                generation_attempted=False
                try:
                    prompt = (received_prompt or '\nCURRENT TURN\n'+check_json(received_packet)
                              if received_packet is not None else self.build_prompt(prepared))
                    self.last_metrics['prompt_bytes'] = len(prompt.encode('utf-8'))
                    if prepared.get('documentary_canon') is not None:
                        self.last_metrics['canon_selection']=prepared['documentary_canon']['search']
                    generation_start = time.monotonic()
                    if received is None:
                        # Reserve before any call; even an outage or /retry does not give this credit back.
                        with self.campaign.transaction() as con:
                            blocked=RecoveryBudget(con,request_id).reserve_generation()
                        if blocked:return blocked
                        self.last_metrics['model_calls'] += 1
                        self._save_attempt(request_id,'in_progress')
                        generation_attempted=True
                        def receive(raw):
                            self._save_attempt(request_id,'receipt',{
                                'raw_received':json.dumps(raw,ensure_ascii=True,allow_nan=False),
                                'received':json.dumps(raw,ensure_ascii=True,allow_nan=False),
                                'prepared':prepared,'packet':json.loads(prompt.rsplit('\nCURRENT TURN\n',1)[1]),
                                'prompt':prompt,'expected_versions':expected_versions})
                        self.narrator.receipt_callback=receive
                        try:
                            self._phase = 'generation'
                            response = self.narrator.generate(prompt)
                        finally:
                            self.narrator.receipt_callback=None
                    else:
                        response = json.loads(received)
                    response_received=True
                    self._phase = 'validation'
                    diagnostic_id=self._received_response(request_id,response,prompt)
                    self._save_attempt(request_id,'receipt',{
                        'received':json.dumps(response,ensure_ascii=True,allow_nan=False),
                        'prepared':prepared,'packet':json.loads(prompt.rsplit('\nCURRENT TURN\n',1)[1]),
                        'prompt':prompt,
                        'expected_versions':expected_versions})
                    if received is None and generation_attempted:
                        self.last_metrics.update(getattr(self.narrator,'last_metrics',{}))
                        self.last_metrics['model_calls']=getattr(self.narrator,'last_metrics',{}).get('model_calls',1)+self.last_metrics.get('planner',{}).get('model_calls',0)
                    self.last_metrics['generation_seconds'] = round(time.monotonic()-generation_start,3)
                    from .narration_codex import normalize_response
                    response = normalize_response(response)
                    if response['memory_request']:
                        memory_query = response['memory_request']['query']
                        self._save_attempt(request_id,'required_context',reason=memory_query)
                        recovered = self.campaign.recover_memory(request_id,memory_query)
                        with self.campaign.transaction() as con:
                            budget=RecoveryBudget(con,request_id)
                            if budget.generation_exhausted():
                                recovered=budget.blocked('The narrator still asks for sources after the enriched generation.')
                        self.last_metrics['total_seconds'] = round(time.monotonic()-started,3)
                        return recovered
                    proposal = self._proposal(prepared,response,json.loads(prompt.rsplit('\nCURRENT TURN\n',1)[1]))
                    self._diagnostic_corrections(diagnostic_id)
                    self._save_attempt(request_id,'prepare',{'proposal':proposal,'memory_updates':self._memory_updates,
                                                           'creation_updates':self._creation_updates,
                                                           'location_reminders':self._location_reminders,
                                                           'agenda_changes':self._agenda_changes,'expected_versions':expected_versions})
                except (ModelUnavailable,ValueError,TypeError,KeyError,KeyboardInterrupt) as exc:
                    if received is None and generation_attempted:
                        self.last_metrics.update(getattr(self.narrator,'last_metrics',{}))
                    self.last_metrics['model_calls']=(getattr(self.narrator,'last_metrics',{}).get('model_calls',1)
                        if received is None and generation_attempted else 0)+self.last_metrics.get('planner',{}).get('model_calls',0)
                    reason=(type(exc).__name__+': '+str(exc)).replace(chr(0x2014),'-')
                    if generation_attempted and diagnostic_id is None and prompt and getattr(self.narrator,'last_response',None) is not None:
                        diagnostic_id=self._received_response(request_id,self.narrator.last_response,prompt)
                    elif generation_attempted and diagnostic_id is None and prompt and getattr(self.narrator,'last_error',None) is not None:
                        diagnostic_id=self._received_response(request_id,{'cli_error':self.narrator.last_error},prompt)
                    if diagnostic_id is not None:
                        self._diagnostic_corrections(diagnostic_id)
                        self._diagnostic_error(diagnostic_id,reason)
                    self._save_attempt(request_id,'failure' if generation_attempted or received is not None else 'preparation_refused',reason=reason)
                    if isinstance(exc,KeyboardInterrupt):
                        raise
                    message=('Answer received but refused by the local validation; input kept. '
                             if response_received else 'Generation interrupted or invalid; input kept. ')
                    raise ModelUnavailable(message+str(exc)) from exc
            application_start = time.monotonic()
            self._phase = 'application'
            result = self.campaign.apply(proposal,memory_updates=self._memory_updates,
                                         creation_updates=getattr(self,'_creation_updates',[]),
                                         location_reminders=getattr(self,'_location_reminders',[]),
                                         agenda_changes=self._agenda_changes,expected_versions=expected_versions)
            self.last_metrics['application_seconds'] = round(time.monotonic()-application_start,3)
            self.last_metrics['total_seconds'] = round(time.monotonic()-started,3)
            self._save_attempt(request_id,result['status'],{'proposal':proposal,'memory_updates':self._memory_updates,
                                                         'creation_updates':getattr(self,'_creation_updates',[]),
                                                         'location_reminders':getattr(self,'_location_reminders',[]),
                                                         'agenda_changes':self._agenda_changes,'expected_versions':expected_versions})
            # Only the text returned by the transaction can be displayed.
            return {key:value for key,value in result.items() if key!='applied_events'}
