"""Prepare a bounded resume after the documentary fix, without playing the action."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

PKG_PARENT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PKG_PARENT))
from novel_engine.campaign import Campaign
from novel_engine.canon_selection import select_for_turn,save_context
from novel_engine.contracts import check_json
from novel_engine.maintenance import backup_before_write,parser
from novel_engine.narrative import narrative_lock
from novel_engine.recovery import RecoveryBudget,exists

REPAIR_ID='documentary_loops_20261015_v1'
TECHNICAL={'action_recovery_budget','action_recovery_roots','memory_recovery','canon_selection_contexts'}


def fingerprint(con):
    digest=hashlib.sha256()
    for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        name=row[0]
        if name in TECHNICAL:continue
        safe='"'+name.replace('"','""')+'"'
        digest.update(name.encode('utf-8'))
        for text in sorted(repr(tuple(values)) for values in con.execute('SELECT * FROM '+safe)):
            digest.update(text.encode('utf-8'))
    return digest.hexdigest()


def plan(con):
    session=json.loads(con.execute('SELECT payload FROM session WHERE id=1').fetchone()[0])
    pending=session.get('pending_action')
    if not pending:raise ValueError('No pending action to repair.')
    rid=pending['request_id']
    root=rid
    if exists(con,'action_recovery_roots'):
        mapped=con.execute('SELECT root_id FROM action_recovery_roots WHERE request_id=?',(rid,)).fetchone()
        if mapped:root=mapped[0]
    state,config=map(json.loads,con.execute('SELECT state,config FROM campaign WHERE id=1').fetchone())
    if not exists(con,'action_recovery_budget'):raise ValueError('No blocked budget recorded.')
    row=con.execute('SELECT generations,searches,history FROM action_recovery_budget WHERE request_id=?',(root,)).fetchone()
    if not row:raise ValueError('No budget recorded for this action.')
    history=json.loads(row[2])
    previous=next((item for item in history if item.get('repair_id')==REPAIR_ID),None)
    if previous:
        return {'status':'already_applied','request_id':rid,'state_version':state['state_version'],
                'limits':previous['limits'],'counts':{'generations':row[0],'searches':row[1]}}
    if row[0]<2 and row[1]<2:raise ValueError('The current budget is not exhausted.')
    attempt=con.execute('SELECT status,proposal FROM narrative_attempts WHERE request_id=?',(rid,)).fetchone()
    if not attempt or attempt[0]!='required_context':
        raise ValueError('This repair is reserved for a documentary blockage; keep every usable receipt.')
    receipt=json.loads(attempt[1] or '{}')
    query='canon: '+pending['text']
    packet=select_for_turn(state,config,pending['text'],session['scene'],query=query)
    if not packet or not packet['facts']:raise ValueError('No corrected documentary context available.')
    old_refs={item['reference'] for item in receipt.get('packet',{}).get('documentary_canon',{}).get('facts',[])}
    refs={item['reference'] for item in packet['facts']}
    added=sorted(refs-old_refs)
    if not added and 'DIALOGUE AND LEARNING' in receipt.get('prompt',''):
        raise ValueError('No new dialogue enrichment or fix compared with the last call.')
    limits={'generations':row[0]+2,'searches':row[1]+2}
    sources={name:hashlib.sha256((PKG_PARENT/name).read_bytes()).hexdigest() for name in
             ('novel_engine/canon_selection.py','novel_engine/instructions.py','novel_engine/recovery.py')}
    return {'status':'preview','request_id':rid,'state_version':state['state_version'],
            'counts':{'generations':row[0],'searches':row[1]},'limits':limits,
            'new_references':added,'query':query,'packet':packet,'sources':sources}


def repair(path,apply=False):
    path=Path(path).resolve()
    campaign=Campaign(path)
    if not path.is_file():raise ValueError('Existing save expected.')
    def preview():
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as con:
            return plan(con)
    report=preview()
    if not apply or report['status']=='already_applied':
        return {key:value for key,value in report.items() if key not in ('packet','query')}
    with narrative_lock(campaign):
        # Do not produce a second save or new credits for the same repair.
        report=preview()
        if report['status']=='already_applied':return report
        with backup_before_write(campaign,'documentary_resume') as backup:
            with campaign.transaction() as con:
                report=plan(con)
                before=fingerprint(con)
                rid=report['request_id'];budget=RecoveryBudget(con,rid)
                budget.record_search(report['query'],report['packet'],False)
                history=budget.state()['history']
                history.append({'repair_id':REPAIR_ID,'state_version':report['state_version'],
                    'query':report['query'],'references':report['new_references'],
                    'limits':report['limits'],'counts_before':report['counts'],
                    'sources':report['sources'],'backup':str(backup),
                    'reason':('Documentary context and instructions corrected; explicit bounded resume, without '
                                 'erasing the attempts.')})
                con.execute('UPDATE action_recovery_budget SET history=? WHERE request_id=?',(check_json(history),budget.request_id))
                save_context(con,rid,campaign._state(con),'narrator',report['packet'])
                con.execute('''INSERT OR REPLACE INTO memory_recovery VALUES (?,?,?,?,?,?)''',
                    (rid,report['query'],'partial',budget.state()['searches'],check_json(report['packet']),
                     ("Documentary context corrected. Answer the current question without adding a date "
                      "requirement absent from the request; preserve the sources' caveats. /resume allows "
                      "the generation, with no new goal.")))
                if before!=fingerprint(con):raise ValueError('The repair modified protected data.')
                if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Incorrect SQLite integrity.')
                remaining={key:report['limits'][key]-budget.state()[key] for key in ('generations','searches')}
            return {'status':'prepared_resume','request_id':rid,'state_version':report['state_version'],
                    'backup':str(backup),'game_data_preserved':True,'remaining':remaining,
                    'counts_before':report['counts'],'limits':report['limits'],
                    'new_references':report['new_references'],'remote_calls':0}


def main(argv=None):
    args=parser(__doc__).parse_args(argv)
    print(json.dumps(repair(args.campaign,args.apply),ensure_ascii=False,indent=2))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
