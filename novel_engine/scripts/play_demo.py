"""Terminal interface: background work continues during input()."""
from pathlib import Path
import argparse
import sys

PKG_PARENT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PKG_PARENT))
from novel_engine.contracts import ROOT
from novel_engine.campaign import Campaign
from novel_engine.demo import initial_state, configuration, action, task, handle_background
from novel_engine.background import Background


def main():
    parser = argparse.ArgumentParser(description='Fictional local demo, with no model call.')
    parser.add_argument('--campaign',default=str(ROOT/'campaigns/demo/campaign.sqlite'))
    args = parser.parse_args()
    campaign = Campaign(args.campaign)
    if not campaign.path.exists():
        campaign.create(initial_state(),configuration())
    campaign.recover_tasks()
    campaign.open_scene('You are in front of the city gates.' if campaign.state()['positions']['player']=='town_gate' else 'You are on the road, outside the city.')
    background = Background(campaign,handle_background)
    background.start()
    print('Fictional demo. Actions: leave, wait 2, memories, quit.')
    print('Your progress is saved. Reading does not advance fictional time.')
    try:
        while True:
            saved=campaign.resume()['session']
            if saved['pending_action']:
                pending=saved['pending_action']
                previous=campaign.result(pending['request_id'])
                if previous and previous['status']=='rejected':
                    pending=campaign.reevaluate_action()
                p=action(campaign.state(),pending['text']);p['request_id']=pending['request_id']
                recovered=background.submit_player(p)
                if recovered['status'] not in ('recorded','question'):
                    print('A saved action needs a re-evaluation by the narrator.')
                    break
                saved=campaign.resume()['session']
            state = campaign.state()
            print('\n'+saved['scene'])
            background.reading(max_tasks=4,max_calls=0)
            # In series: a first mutation could make the second task obsolete.
            spec = task(state,'expiration') or task(state)
            if spec:
                try:
                    background.enqueue(spec)
                except ValueError:
                    pass
            try:
                text = input('\nYour action: ')
            except EOFError:
                break
            background.interrupt()
            if text.strip().casefold()=='quit':
                break
            if text.strip().casefold()=='memories':
                for e in campaign.journal('player'):
                    print(e['description'])
                continue
            pending=campaign.begin_action(text)
            p=action(campaign.state(),text);p['request_id']=pending['request_id']
            result = background.submit_player(p)
            if result['status'] not in ('recorded','question'):
                print('This action was not recorded. Please rephrase it.')
                break
            background.settle_prepared()
    except KeyboardInterrupt:
        print(('\n'
               'Game saved.'))
    finally:
        background.close()
        campaign.export_resume()


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
