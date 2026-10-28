"""Bounded technical decisions and public diagnostic through a positive list."""
import json

from .contracts import check_json
from .recovery import RecoveryBudget

POLICY = 'bounded_v1'


def public_metrics(metrics):
    keys = ('model_calls','narrative_calls','planning_calls','prompt_bytes','total_seconds',
            'automatic_recoveries','preparation_seconds','generation_seconds','application_seconds',
            'budget_ratio','alert_budget')
    return {k:v for k,v in metrics.items() if k in keys and type(v) in (int,float,bool)}


def classify(exc, narrator, phase):
    error = getattr(narrator, 'last_error', None) or {}
    category = error.get('category')
    if phase == 'application': return 'local_application', False
    if phase == 'preparation': return 'preparation_or_process', False
    if phase == 'validation':
        if isinstance(exc.__cause__, (ValueError, TypeError, KeyError)):
            return 'answer_contract', True
        return 'local_validation', False
    if category == 'timeout':
        return 'delay', error.get('process_stopped') is True
    if category in ('service','service_unavailable','rate_limit','server_error'):
        return 'transient_service', error.get('process_stopped') is True
    if category in ('auth','authentication','permissions','permission','access_denied'):
        return 'access', False
    # A complete output refused by the contract can receive a single correction.
    if category == 'invalid_response': return 'answer_contract', True
    return 'preparation_or_process', False


def transition(campaign, request_id, phase, code, continuation):
    with campaign.transaction() as con:
        budget = RecoveryBudget(con, request_id)
        con.execute('''CREATE TABLE IF NOT EXISTS recovery_transitions (
            seq INTEGER PRIMARY KEY, root_id TEXT NOT NULL, request_id TEXT NOT NULL,
            payload TEXT NOT NULL)''')
        value = {'policy':POLICY, 'phase':phase, 'code':code, 'continue':bool(continuation),
                 'generations':budget.state()['generations'], 'searches':budget.state()['searches']}
        con.execute('INSERT INTO recovery_transitions(root_id,request_id,payload) VALUES (?,?,?)',
                    (budget.request_id,request_id,check_json(value)))
        return value


def stopped(campaign, request_id, code, metrics=None, phase=None):
    # No raw message, prompt, response, private name or key is exported.
    with campaign.transaction() as con:
        budget = RecoveryBudget(con,request_id)
        state = budget.state()
        versions = campaign._state(con)['versions']
    diagnostic = {'diagnostic_version':'1', 'code':code, 'policy':POLICY,
        'phase':phase if phase in ('preparation','generation','validation','search','application') else 'unknown',
        'budget':{k:state[k] for k in ('generations','searches')},
        'versions':{k:v for k,v in versions.items() if k.endswith('_revision')},
        'measures':public_metrics(metrics or {})}
    path = campaign.path.with_suffix('.diagnostic.json')
    path.write_text(json.dumps(diagnostic,ensure_ascii=True,indent=2),encoding='utf-8')
    return {'status':'technical_stop', 'request_id':request_id, 'diagnostic_code':code,
        'reason':'Action and scene kept. Local diagnostic available; no new consequence can be displayed.',
        'operation_possible':'fix_then_resume' if code=='local_application' else 'diagnostic_local',
        'diagnostic_path':str(path), 'recovery_budget':diagnostic['budget']}
