"""Interactive game or a single narrative turn, with no new API key."""
import argparse
import json
from pathlib import Path
import sys

PKG_PARENT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PKG_PARENT))
from novel_engine.adapters import ModelUnavailable
from novel_engine.campaign import Campaign
from novel_engine.engines import ENGINES, create, default_engine
from novel_engine.narration_codex import DEFAULT_NARRATIVE_TIMEOUT
from novel_engine.narrative import NarrativeLoop
from novel_engine.resume import active_campaign


def choose_engine(preference=None):
    """LLM choice when the game is launched in the terminal; Enter keeps the last choice, else the first one available."""
    default=default_engine(preference)
    while True:
        print('Narrator engine: '+', '.join(ENGINES)+'? ['+default+'] ',end='',file=sys.stderr,flush=True)
        try:
            answer=input().strip().casefold()
        except EOFError:
            return default
        if answer in ('',*ENGINES):
            return answer or default
        print('Type one of: '+', '.join(ENGINES)+'.',file=sys.stderr)


def read_engine_preference(path):
    try:
        value=json.loads(Path(path).read_text(encoding='utf-8')).get('engine')
    except (OSError,ValueError,AttributeError):
        return None
    return value if value in ENGINES else None


def write_engine_preference(path,engine):
    try:
        Path(path).write_text(json.dumps({'engine':engine}),encoding='utf-8')
    except OSError:
        pass


def main(argv=None):
    parser=argparse.ArgumentParser(description='Play turns: each action is saved before its narration is shown.')
    parser.add_argument('--campaign',help='Path of a campaign.sqlite (the active campaign by default)')
    parser.add_argument('--text',help='Play one action and exit')
    parser.add_argument('--text-file',help='Read the action from a UTF-8 file, which avoids quoting problems in the terminal')
    parser.add_argument('--resume',action='store_true',help='Process the pending action')
    parser.add_argument('--reevaluate',action='store_true',help='Re-evaluate a rejected action')
    parser.add_argument('--retry',action='store_true',help='Allow a new model call after a failure or an interruption (with --resume)')
    parser.add_argument('--result',help='Show a recorded result again, with no model call')
    parser.add_argument('--engine',choices=ENGINES,help='Engine of the narrator and the planner; asked when the game starts in a terminal, else the saved choice or the first engine available (manual needs no account)')
    parser.add_argument('--model',help='Model name (default: gpt-6.1-sol for Codex, claude-sonnet-5-5 for Claude and the API)')
    parser.add_argument('--effort',choices=('low','medium','high','xhigh','max'),default='low',help='Reasoning effort of the narrator')
    parser.add_argument('--delay',type=int,default=DEFAULT_NARRATIVE_TIMEOUT,help='Maximum time per generation, in seconds')
    parser.add_argument('--words',type=int,default=180,help='Target length of ordinary narrations, in words')
    parser.add_argument('--planner-effort',choices=('low','medium','high','xhigh','max'),default='medium',help='Reasoning effort of the periodic review')
    parser.add_argument('--planner-delay',type=int,default=180,help='Maximum time of the periodic review, in seconds')
    parser.add_argument('--codex',help='Path of codex.exe if missing from PATH')
    parser.add_argument('--claude',help='Path of claude.exe if missing from PATH')
    parser.add_argument('--search',default='',help='Extra subject to look up in memory for this turn')
    parser.add_argument('--memories',help="Search the character's memories, with no turn and no model")
    parser.add_argument('--topics',action='store_true',help='List the memory topics the character can look up, with no turn')
    parser.add_argument('--files',dest='topics',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--creations',help='Look up invented NPCs, places and quests by name, alias or place, with no turn')
    parser.add_argument('--agenda',action='store_true',help='Show the deadlines the character knows, with no turn')
    parser.add_argument('--summaries',action='store_true',help='Show the public summaries, with no turn and no model')
    parser.add_argument('--inventory',action='store_true',help='Show possessions, purse and projects, with no turn')
    parser.add_argument('--skills',nargs='?',const='',help='Show abilities, costs and descriptions, optionally for one subject')
    parser.add_argument('--json',action='store_true',help='Print the result as JSON (for scripts and chat assistants)')
    parser.add_argument('--measures',action='store_true',help='Print durations and usage on stderr')
    args=parser.parse_args(argv)
    if args.text is not None and args.text_file:
        parser.error('Choose --text or --text-file.')
    if args.retry and not args.resume:
        parser.error('--retry requires --resume.')
    text=Path(args.text_file).read_text(encoding='utf-8') if args.text_file else args.text
    if args.campaign:
        path=Path(args.campaign)
        if not path.is_file():
            parser.error('Existing save expected; no automatic initialization.')
        campaign=Campaign(path)
    else:
        _,campaign=active_campaign()
        if campaign is None:
            parser.error('No active campaign.')
    def display_memories(result):
        if args.json:
            print(json.dumps(result,ensure_ascii=False,separators=(',',':')))
        elif result['status']=='skills':
            from novel_engine.abilities import render
            print(render(result))
        elif result['status']=='agenda':
            print('Clock: '+result['clock']['date'])
            for item in result['events']:
                print(item['date']+': '+item['title']+' ('+item['status']+')')
            if not result['events']:
                print('No known deadline recorded.')
        elif result['status']=='topics':
            for item in result['topics']:
                print(item['id']+': '+item['title'])
        elif result['status']=='creations':
            for item in result['creations']:
                print(item['name']+' ('+item['status']+'): '+'; '.join(item['traits']))
            if not result['creations']: print('No accessible creation found.')
        elif result['status']=='summaries':
            for item in result['summaries']:
                print('\n['+item['reference']+']\n'+item['text'])
            if not result['summaries']:print('No summary recorded.')
        elif result['status']=='inventory':
            for value in result['purse'].values():
                print(value['label'] + ': ' + str(value['balance']))
            for key, value in result['possessions'].items():
                quantity = 'not counted' if value['quantity'] is None else str(value['quantity'])
                print(key + ': ' + quantity + ' (' + value.get('quantity_precision', 'exact') + '), '
                      + value.get('location', 'place unknown') + '. ' + value['description'])
            for key, value in result['projects'].items():
                print('\n' + value['title'] + ' (' + value['status'] + ')')
                for label, field in [('Done', 'completed'), ('To do', 'remaining'), ('To check', 'unknowns')]:
                    print(label + ': ' + '; '.join(value[field]))
        else:
            for item in result['proofs']:
                print('\n['+item['reference']+']\n'+item['text'])
            if not result['proofs']:
                print('No accessible memory found for this search.')
        return 0
    if args.memories is not None or args.topics or args.agenda or args.summaries or args.inventory or args.creations is not None or args.skills is not None:
        if text is not None or args.resume or args.reevaluate or args.result:
            parser.error('A local consultation cannot resolve an action.')
        if args.agenda:
            return display_memories(campaign.world_agenda())
        if args.skills is not None:
            return display_memories(campaign.skills(args.skills))
        if args.summaries:
            return display_memories(campaign.world_summaries())
        if args.inventory:
            return display_memories(campaign.possessions())
        if args.creations is not None:
            return display_memories(campaign.creations(args.creations))
        return display_memories(campaign.memories(args.memories or '',topics=args.topics))
    if args.engine is None:
        interactive=text is None and not (args.resume or args.reevaluate or args.result or args.json) and sys.stdin.isatty()
        if interactive:
            preference_path=campaign.path.parent/'novel_engine.json'
            args.engine=choose_engine(read_engine_preference(preference_path))
            write_engine_preference(preference_path,args.engine)
        else:
            args.engine=default_engine(read_engine_preference(campaign.path.parent/'novel_engine.json'))
    narrator,planner,model=create(args.engine,campaign.path.parent,args.model,args.effort,args.planner_effort,
                                  args.delay,args.planner_delay,args.codex,args.claude)
    loop=NarrativeLoop(campaign,narrator,args.words,planner=planner)

    def display(result):
        from novel_engine.bounded_resume import POLICY, public_metrics
        measures=(public_metrics(loop.last_metrics) if campaign.config().get('recovery_policy')==POLICY
                  else {key:value for key,value in loop.last_metrics.items() if key!='context_diagnostic'})
        if args.json:
            print(json.dumps({**result,'measures':measures},ensure_ascii=False,separators=(',',':')))
        elif result['status'] in ('recorded','question','resume'):
            print('\n\n'+result['narration']+'\n\n',end='',flush=True)
        elif result['status'] in ('memory_required','technical_stop'):
            print(result['reason'],file=sys.stderr)
            if result.get('diagnostic_path'): print('Diagnostic: '+result['diagnostic_path'],file=sys.stderr)
        else:
            print('Turn refused: '+result['reason'],file=sys.stderr)
        if args.measures:
            print(json.dumps(measures,ensure_ascii=False,separators=(',',':')),file=sys.stderr)
        return 0 if result['status'] in ('recorded','question','resume','memory_required') else 2

    if args.result:
        result=campaign.result(args.result)
        if result is None:
            parser.error('Recorded result not found.')
        return display({key:value for key,value in result.items() if key!='applied_events'})
    if text is not None or args.resume or args.reevaluate:
        return display(loop.run_turn(text,args.resume,args.reevaluate,args.retry,args.search))
    if args.json:
        return display(loop.run_turn())

    # Load the documents with a renewable signature; the world's memory
    # is reread transactionally at every action.
    loop._documents()
    display(loop.run_turn())
    print('\nCommands: /quit, /resume, /retry, /reevaluate, /memories <subject>, /topics, /creations [subject], /reminder <subject>, /agenda, /summaries, /inventory, /purse, /projects, /skills [subject].',file=sys.stderr)
    extra_query=args.search
    if campaign.resume()['session']['pending_action']:
        print('An action is already waiting. /resume processes it explicitly.',file=sys.stderr)
    while True:
        try:
            text=input('\nYour action: ')
        except EOFError:
            return 0
        command=text.strip().casefold()
        if command=='/quit':
            return 0
        if not command:
            continue
        try:
            name,_,argument=text.strip().partition(' ')
            name=name.casefold()
            if name=='/skills':
                display_memories(campaign.skills(argument))
                continue
            if name=='/agenda':
                display_memories(campaign.world_agenda())
                continue
            if name=='/summaries':
                display_memories(campaign.world_summaries())
                continue
            if name in ('/inventory', '/purse', '/projects'):
                result = campaign.possessions()
                if name == '/purse':
                    result.update(possessions={}, projects={})
                elif name == '/projects':
                    result.update(possessions={}, purse={})
                display_memories(result)
                continue
            if name in ('/memories','/topics'):
                display_memories(campaign.memories(argument,topics=name=='/topics'))
                continue
            if name=='/creations':
                display_memories(campaign.creations(argument))
                continue
            if name=='/reminder':
                if not argument.strip():
                    print('Give the subject after /reminder.',file=sys.stderr)
                    continue
                extra_query=argument.strip()
                pending=campaign.resume()['session']['pending_action']
                if pending:
                    recovery=campaign.recover_memory(pending['request_id'],extra_query)
                    print(recovery['reason'],file=sys.stderr)
                print('Search subject specified. No game action performed.',file=sys.stderr)
                continue
            if command.startswith('/') and command not in ('/resume','/retry','/reevaluate'):
                print('Unknown command. No turn created.',file=sys.stderr)
                continue
            print('The narrator is preparing the answer...',file=sys.stderr,flush=True)
            if command in ('/resume','/retry','/reevaluate'):
                result=loop.run_turn(resume=command!='/reevaluate',reevaluate=command=='/reevaluate',
                                     retry=command=='/retry',query=extra_query)
            else:
                result=loop.run_turn(text,query=extra_query)
            display(result)
            if result['status'] in ('recorded','question'):
                extra_query=args.search
        except (ModelUnavailable,ValueError) as exc:
            message=str(exc)
            for option,interactive in (('--resume --retry','/retry'),
                                       ('--resume','/resume'),('--reevaluate','/reevaluate')):
                message=message.replace(option,interactive)
            print(message,file=sys.stderr)


if __name__=='__main__':
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    try:
        sys.exit(main())
    except (ModelUnavailable,ValueError,OSError) as exc:
        print(str(exc),file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        print(('\n'
               'Interrupted. Input kept; no automatic retry.'),file=sys.stderr)
        sys.exit(130)
