"""Second process for the tests: lock, concurrent write and recall against the same save."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from novel_engine.adapters import ModelUnavailable
from novel_engine.campaign import Campaign
from novel_engine.narrative import narrative_lock

mode, path = sys.argv[1:3]
campaign = Campaign(path)
if mode == 'lock':
    try:
        with narrative_lock(campaign):
            print('free')
    except ModelUnavailable:
        print('locked')
elif mode == 'write':
    from novel_engine.demo import proposal
    print(campaign.apply(proposal(campaign.state(), 'A concurrent event is recorded.'), background=True)['status'])
elif mode == 'recall':
    print(json.dumps({'creations': campaign.creations(sys.argv[3]), 'memory': campaign.memories(sys.argv[3])},
                     ensure_ascii=True))
