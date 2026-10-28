"""Codex narrator with no tools, one structured generation per turn."""
from copy import deepcopy
import json
import re
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from .adapters import ModelUnavailable
from .proc import kill_tree, popen_options
from .contracts import ROOT, check_json, load_schema
from .diagnostic_cli import collect_failure,failure_summary,safe_detail
from .validation import validate


def find_codex():
    """Find the CLI even if PowerShell does not inherit the application's PATH."""
    executable = shutil.which('codex.exe') or shutil.which('codex')
    if executable:
        return executable
    locals = {Path.home()/'AppData'/'Local'}
    if os.environ.get('LOCALAPPDATA'):
        locals.add(Path(os.environ['LOCALAPPDATA']))
    candidates = []
    roots = [local/subpath for local in locals
             for subpath in ('OpenAI/Codex/bin','Programs/OpenAI/Codex/bin')]
    for root in roots:
        try:
            # Updates may leave an old binary at the root.
            for path in [root/'codex.exe',*root.glob('*/codex.exe')]:
                if path.is_file():
                    candidates.append((path.stat().st_mtime_ns,str(path)))
        except OSError:
            continue
    return max(candidates)[1] if candidates else None


MAX_TURN_EVENTS = 64
DEFAULT_NARRATIVE_TIMEOUT = 240


def response_schema(legacy=False, references=None, version='1.1', memory_references=None, creations=False):
    schema = load_schema('proposal')
    for key in ('schema_version','request_id','campaign_id','expected_state_version'):
        del schema['properties'][key]
        schema['required'].remove(key)
    event = schema['properties']['events']['items']
    for key in ('world_second','mechanical_operations','public_consequences'):
        if version=='1.3':
            event['required'].append(key)
        else:
            event['properties'].pop(key,None)
    event['required'].append('asset_operations')
    schema['properties']['events']['maxItems']=MAX_TURN_EVENTS
    del event['properties']['event_id']
    event['required'].remove('event_id')
    # The remote schema uses explicit types; the dynamic values
    # will be decoded and then checked by the engine's full contract.
    for group, old, new in (('conditions','equals','equals_json'),('changes','value','value_json')):
        item = event['properties'][group]['items']
        del item['properties'][old]
        item['properties'][new] = {'type':'string'}
        item['required'].remove(old)
        item['required'].append(new)
    if not legacy:
        schema['properties'].update({
            'narrative_schema_version':{'type':'string','const':'1.0'},
            'memory_request':{'type':['object','null'], 'properties':{
                'query':{'type':'string','minLength':1,'maxLength':800},
                'reason':{'type':'string','minLength':1,'maxLength':800}},
                'required':['query','reason'],'additionalProperties':False},
            'memory_updates':{'type':'array','maxItems':4,'items':{'type':'object','properties':{
                'topic_id':{'type':'string','pattern':'^[a-z0-9_:.-]{1,80}$'},
                'title':{'type':'string','minLength':1,'maxLength':160},
                'aliases':{'type':'array','maxItems':12,'items':{'type':'string','minLength':1,'maxLength':80}},
                'references':{'type':'array','minItems':1,'maxItems':12,'items':{'type':'string','minLength':1}}},
                'required':['topic_id','title','aliases','references'],'additionalProperties':False}}})
        schema['required'] += ['narrative_schema_version','memory_request','memory_updates']
        from .agenda import agenda_schema
        additions=agenda_schema(event['properties']['conditions']['items'])
        schema['properties'].update(additions)
        schema['properties']['narrative_schema_version']={'type':'string','const':version}
        if version in ('1.2','1.3'):
            from .agenda_protocol import command_schema
            schema['properties']['agenda_updates'] = command_schema(additions['agenda_updates'])
        schema['required']+=list(additions)
    if creations:
        from .creations import updates_schema
        schema['properties']['creation_schema_version'] = {'type':'string','const':'1'}
        schema['properties']['creation_updates'] = updates_schema()
        schema['required'] += ['creation_schema_version','creation_updates']
    if references is not None:
        local=['current_event:'+str(i) for i in range(MAX_TURN_EVENTS)]
        choices=sorted(set(references)|set(local)|{'current_action'})
        event['properties']['causes']['items']['enum']=choices
        if not legacy:
            schema['properties']['agenda_updates']['items']['properties']['references']['items']['enum']=choices
            memory_refs=set(memory_references) if memory_references is not None else {ref for ref in references if not ref.startswith(('canon:','plan:'))}
            schema['properties']['memory_updates']['items']['properties']['references']['items']['enum']=sorted(memory_refs|{'current_turn'})
    return schema


def normalize_response(response):
    # Older JSON strings that were encoded twice: two decodings at most.
    for _ in range(2):
        if not isinstance(response,str): break
        response=json.loads(response)
    if not isinstance(response,dict):
        raise ValueError('Narrative JSON object expected.')
    response = deepcopy(response)
    for event in response.get('events', []):
        event.setdefault('asset_operations', [])
    # Compatibility with saved responses and earlier local adapters.
    if set(response) <= {'narration','events','blocking_questions'}:
        response.update(narrative_schema_version='1.0',memory_request=None,memory_updates=[])
    if response.get('narrative_schema_version')=='1.0':
        if any(key in response for key in ('agenda_updates','continuation','roll_checks')):
            raise ValueError('Agenda fields in an old narrative envelope.')
        response.update(narrative_schema_version='1.1',agenda_updates=[],continuation=None,roll_checks=[])
    validate(response_schema(version=response.get('narrative_schema_version', '1.1'),
                             creations='creation_schema_version' in response),response)
    if response['narrative_schema_version'] not in ('1.1', '1.2','1.3'):
        raise ValueError('Version narrative unknown.')
    if response['memory_request'] and (response['events'] or response['blocking_questions'] or response['memory_updates']
                                     or response['agenda_updates'] or response['continuation'] or response['roll_checks']
                                     or response.get('creation_updates')):
        raise ValueError('A memory request forbids events, questions and updates.')
    if response['blocking_questions'] and response.get('creation_updates'):
        raise ValueError('A question forbids creations.')
    return response


def replace_em_dash(value):
    """Replace U+2014 with a simple hyphen in every string of the response; return (value, count)."""
    dash=chr(0x2014)
    if isinstance(value,str):
        if dash not in value:return value,0
        return re.sub(r'\s*'+dash+r'\s*',' - ',value),value.count(dash)
    if isinstance(value,list):
        out=[replace_em_dash(v) for v in value]
        return [v for v,_ in out],sum(n for _,n in out)
    if isinstance(value,dict):
        out={}
        total=0
        for k,v in value.items():
            k2,a=replace_em_dash(k);v2,b=replace_em_dash(v)
            out[k2]=v2;total+=a+b
        return out,total
    return value,0


class CodexNarrator:
    provider = 'Codex'

    def __init__(self, model='gpt-6.1-sol', effort='low', timeout=DEFAULT_NARRATIVE_TIMEOUT, executable=None):
        if effort not in ('low','medium','high','xhigh','max') or not 1 <= timeout <= 600:
            raise ValueError('Invalid narrative effort or timeout')
        self.model, self.effort, self.timeout = model, effort, timeout
        self.executable = executable or find_codex()
        self.schema = response_schema()
        self.last_metrics = {}
        self.last_response = None
        self.last_error = None

    def command(self, schema_path, workdir):
        if not self.executable:
            raise ModelUnavailable('Codex CLI not found; install it or give codex.exe.')
        command = [self.executable,'exec','--ignore-user-config','--ephemeral',
                   '--sandbox','read-only','--skip-git-repo-check','--json','--color','never',
                   '--cd',str(workdir),'--output-schema',str(schema_path),
                   '--model',self.model,'-c',f'model_reasoning_effort="{self.effort}"',
                   '-c','project_doc_max_bytes=0','-c','web_search="disabled"',
                   '-c','skills.max_context_tokens=1']
        for feature in ('shell_tool','shell_snapshot','apps','plugins','hooks','multi_agent',
                        'browser_use','computer_use','image_generation','view_image','sleep_tool',
                        'skill_search','unbounded_connection_retries','fast_mode','ultrafast_mode'):
            command += ['--disable',feature]
        # Player cost preference: Standard enforced on every call.
        command += ['-c','service_tier="standard"']
        return command + ['-']

    def schema_for_prompt(self, prompt):
        references=None
        memory_references=None
        if '\nCURRENT TURN\n' in prompt:
            packet=json.loads(prompt.rsplit('\nCURRENT TURN\n',1)[1])
            references=packet['allowed_references']
            memory_references=packet.get('memory_references')
        version='1.3' if '\nCURRENT TURN\n' in prompt and packet['campaign_rules'].get('mechanics') else '1.2'
        creations = '\nCURRENT TURN\n' in prompt and packet['campaign_rules'].get('creation_format')=='1'
        return response_schema(references=references, version=version, memory_references=memory_references, creations=creations)

    def validate_response(self, response, schema):
        validate(schema,normalize_response(response))

    def generate(self, prompt):
        base = ROOT/'.tmp-narration'
        base.mkdir(exist_ok=True)
        started = time.monotonic()
        self.last_metrics = {'model':self.model,'effort':self.effort,'fast':False,'model_calls':0,
                             'timeout_seconds':self.timeout}
        self.last_response = None
        self.last_error = None
        schema=self.schema_for_prompt(prompt)
        from .schema_codex import compile_schema
        wire_schema, fingerprint = compile_schema(schema)
        self.last_metrics['schema_sha256'] = fingerprint
        with tempfile.TemporaryDirectory(dir=base) as temporary:
            folder = Path(temporary)
            try:
                self.last_response = self.call(prompt, wire_schema, folder, base)
                callback=getattr(self,'receipt_callback',None)
                if callback is not None:
                    callback(self.last_response)
                response = json.loads(self.last_response)
                response,replaced = replace_em_dash(response)
                if replaced:self.last_metrics['em_dashes_replaced']=replaced
                check_json(response)
                self.validate_response(response,schema)
                return deepcopy(response)
            except subprocess.TimeoutExpired as exc:
                raise ModelUnavailable(failure_summary(self.last_error, self.provider)) from exc
            except OSError as exc:
                failure=collect_failure(b'',safe_detail(str(exc)),None)
                self.last_error=failure;self.last_metrics['cli_error']=failure
                raise ModelUnavailable(failure_summary(failure, self.provider)) from exc
            except (ValueError, TypeError, KeyError, UnicodeError) as exc:
                detail=str(exc).replace(chr(0x2014),'-')
                if self.last_error is None:
                    self.last_error=collect_failure(b'',safe_detail(detail),None)
                    self.last_error['category']='invalid_response'
                    self.last_metrics['cli_error']=self.last_error
                raise ModelUnavailable('Response '+self.provider+' invalid; action kept. '+detail) from exc
            finally:
                self.last_metrics['model_seconds'] = round(time.monotonic()-started,3)

    def call(self, prompt, wire_schema, folder, base):
        """JSON text of the model's answer. This transport runs a CLI; other engines override it."""
        command, payload, cwd = self.invocation(prompt, wire_schema, folder, base)
        self.last_metrics['model_calls'] = 1
        process = subprocess.Popen(command,stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=cwd,
            env=self.environment(),
            **popen_options())
        try:
            stdout, stderr = process.communicate(payload,timeout=self.timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as interrupted:
            tree_stopped = kill_tree(process)
            try:stopped_out,stopped_err=process.communicate(timeout=10)
            except (OSError,subprocess.TimeoutExpired) as stopping:
                stopped_out=getattr(stopping,'output',None) or getattr(interrupted,'output',b'')
                stopped_err=getattr(stopping,'stderr',None) or getattr(interrupted,'stderr',b'')
            self.last_error=collect_failure(stopped_out,stopped_err,process.returncode)
            self.last_error['category']='interrupted' if isinstance(interrupted,KeyboardInterrupt) else 'timeout'
            self.last_error['timeout_seconds']=self.timeout
            self.last_error['process_stopped']=process.poll() is not None and tree_stopped
            self.last_metrics['cli_error']=self.last_error
            raise
        return self.read_output(stdout, stderr, process)

    def invocation(self, prompt, wire_schema, folder, base):
        """Command, UTF-8 input and working folder of a call."""
        schema_path = folder/'answer.schema.json'
        schema_path.write_text(check_json(wire_schema),encoding='utf-8')
        # Stable directory to keep a stable context prefix
        # between turns; the schema stays specific to each call.
        return self.command(schema_path,base), prompt.encode('utf-8'), None

    def environment(self):
        return {**os.environ,'PYTHONIOENCODING':'utf-8','PYTHONUTF8':'1'}

    def read_output(self, stdout, stderr, process):
        """JSON text of the response, after checking the CLI errors."""
        failure=collect_failure(stdout,stderr,process.returncode)
        failure['process_stopped']=process.poll() is not None
        if process.returncode or any(e in ('turn.failed','error') for e in failure['events']):
            self.last_error=failure
            self.last_metrics['cli_error']=failure
            raise ModelUnavailable(failure_summary(failure))
        messages = []
        diagnostics = []
        for line in stdout.decode('utf-8').splitlines():
            event = json.loads(line)
            if event.get('type') == 'item.completed':
                item = event['item']
                if item['type'] == 'agent_message':
                    messages.append(item['text'])
                elif item['type'] == 'error':
                    diagnostics.append(item.get('message','CLI error'))
                elif item['type'] != 'reasoning':
                    raise ModelUnavailable('Non-narrative Codex item ('+item['type']+'); response refused.')
            elif event.get('type') == 'turn.completed':
                self.last_metrics['usage'] = event.get('usage',{})
            elif event.get('type') in ('turn.failed','error'):
                raise ModelUnavailable('The Codex generation failed; action kept.')
        if not messages:
            failure['category']='invalid_response'
            if diagnostics and not failure['detail']:failure['detail']=safe_detail(str(diagnostics[:1]))
            self.last_error=failure;self.last_metrics['cli_error']=failure
            raise ModelUnavailable(failure_summary(failure))
        self.last_metrics['cli_warning_count'] = len(diagnostics)
        return messages[-1]
