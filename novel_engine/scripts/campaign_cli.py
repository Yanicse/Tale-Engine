"""JSON commands linking the tools and a chat assistant to a local campaign."""
import argparse
import json
from pathlib import Path
import sys

PKG_PARENT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PKG_PARENT))
from novel_engine.contracts import ROOT
from novel_engine.campaign import Campaign
from novel_engine.demo import initial_state,configuration,action
from novel_engine.memory import ContextBuilder,CanonRepository
from novel_engine.resume import active_campaign


def migrate_schema(campaign,apply):
    """The only migration route for an existing database: never during a resume."""
    from contextlib import closing
    import sqlite3,time,uuid
    from novel_engine import schema
    from novel_engine.narrative import narrative_lock
    with closing(sqlite3.connect(campaign.path.resolve().as_uri()+'?mode=ro',uri=True)) as con:
        before=schema.version(con);pending=schema.pending(con)
    output={'version':before,'version_engine':schema.CURRENT,'pending_migrations':[n for n,_ in pending]}
    if not apply or not pending:return output
    backup=campaign.path.parent/'saves'/f'before_schema{schema.CURRENT}_{time.strftime("%Y%m%d_%H%M%S")}_{uuid.uuid4().hex[:8]}.sqlite'
    backup.parent.mkdir(exist_ok=True)
    with narrative_lock(campaign):
        with closing(sqlite3.connect(campaign.path)) as con:
            con.execute('VACUUM INTO ?',(str(backup),))
            con.execute('BEGIN IMMEDIATE')
            output['applied']=schema.migrate(con)
            con.commit()
    output.update(version=schema.CURRENT,save=str(backup))
    return output


def main():
    parser=argparse.ArgumentParser(description='Low-level JSON commands on a campaign (the active one by default).')
    parser.add_argument('--campaign')
    parser.add_argument('--compact',action='store_true',help='JSON without indentation')
    subs=parser.add_subparsers(dest='command',required=True)
    p=subs.add_parser('init');p.add_argument('--initial-state');p.add_argument('--configuration')
    subs.add_parser('state')
    subs.add_parser('agenda',help='Deadlines accessible to the player, with no fictional advance')
    p=subs.add_parser('action-demo');p.add_argument('--text',required=True)
    p=subs.add_parser('apply');p.add_argument('--file',required=True,help='UTF-8 JSON, or - for stdin')
    p.add_argument('--short',action='store_true',help='Return without copying the applied events')
    p=subs.add_parser('prepare-turn',help='Input, resume, state and private context in one call')
    p.add_argument('--text',help='Player action; omit to resume without a new input')
    p.add_argument('--search',default='')
    p.add_argument('--exchanges',type=int,default=12)
    p.add_argument('--context-limit',type=int,default=100)
    p.add_argument('--reevaluate',action='store_true',help='New identifier for an action that was already rejected')
    p=subs.add_parser('context');p.add_argument('--recipient',default='player');p.add_argument('--search',default='')
    p=subs.add_parser('journal');p.add_argument('--recipient',default='player')
    subs.add_parser('resume')
    subs.add_parser('session')
    p=subs.add_parser('migrate-schema',help='SQLite schema version; --apply backs up then migrates');p.add_argument('--apply',action='store_true')
    subs.add_parser('reevaluate-action')
    p=subs.add_parser('open-scene');p.add_argument('--file',required=True)
    p=subs.add_parser('enter-action');p.add_argument('--text',required=True)
    p=subs.add_parser('search-canon');p.add_argument('--search',required=True);p.add_argument('--sources',action='store_true')
    args=parser.parse_args()
    # Only init and action-demo work on the fictional demo save; every other command reads the active campaign.
    if args.command not in ('init','action-demo') and args.campaign is None:
        _,campaign=active_campaign()
        if campaign is None:
            parser.error('No active campaign; select an existing save.')
    else:
        campaign=Campaign(args.campaign or ROOT/'campaigns/demo/campaign.sqlite')
    if args.command=='prepare-turn' and not campaign.path.is_file():
        parser.error('Existing save expected; no automatic initialization.')
    if args.command=='init':
        state=json.loads(Path(args.initial_state).read_text(encoding='utf-8')) if args.initial_state else initial_state()
        config=json.loads(Path(args.configuration).read_text(encoding='utf-8')) if args.configuration else configuration()
        campaign.create(state,config);output={'status':'created','file':str(campaign.path)}
    elif args.command=='state':output=campaign.state()
    elif args.command=='agenda':output=campaign.world_agenda()
    elif args.command=='action-demo':
        pending=campaign.begin_action(args.text)
        p=action(campaign.state(),args.text);p['request_id']=pending['request_id']
        output=campaign.apply(p)
    elif args.command=='apply':
        raw=sys.stdin.read() if args.file=='-' else Path(args.file).read_text(encoding='utf-8')
        payload=json.loads(raw)
        if 'proposal' in payload:
            if set(payload)-{'proposal','agenda_changes','memory_updates','creation_updates'}:
                raise ValueError('Unknown field in the manual application envelope.')
            output=campaign.apply(payload['proposal'],agenda_changes=payload.get('agenda_changes'),
                memory_updates=payload.get('memory_updates'),creation_updates=payload.get('creation_updates'))
        else:
            output=campaign.apply(payload)
        if args.short:
            output={key:value for key,value in output.items() if key!='applied_events'}
    elif args.command=='prepare-turn':
        output=campaign.prepare_turn(args.text,args.search,args.exchanges,args.context_limit,args.reevaluate)
        output['campaign_path']=str(campaign.path)
    elif args.command=='context':output=ContextBuilder(campaign).build(args.recipient,args.search)
    elif args.command=='journal':output=campaign.journal(args.recipient)
    elif args.command=='resume':output={'file':str(campaign.export_resume())}
    elif args.command=='session':output=campaign.resume()
    elif args.command=='migrate-schema':output=migrate_schema(campaign,args.apply)
    elif args.command=='reevaluate-action':output=campaign.reevaluate_action()
    elif args.command=='open-scene':output=campaign.open_scene(Path(args.file).read_text(encoding='utf-8'))
    elif args.command=='enter-action':output=campaign.begin_action(args.text)
    else:
        state=campaign.state()
        repo=CanonRepository(state['versions']['canon_snapshot'],state['versions']['canon_revision'])
        output={'usage':'preparation, no knowledge attributed automatically',
                'results':repo.search_sources(args.search) if args.sources else repo.search(args.search)}
    compact=args.compact or args.command=='prepare-turn'
    print(json.dumps(output,ensure_ascii=False,indent=None if compact else 2,
                     separators=(',',':') if compact else None))


if __name__=='__main__':
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    main()
