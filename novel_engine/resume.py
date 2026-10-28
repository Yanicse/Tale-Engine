"""Explicit campaign selection, independent of a chat's memory."""
import json
from pathlib import Path
import uuid

from .campaign import Campaign
from .contracts import ROOT, check_json

ACTIVE = ROOT / 'campaigns/active.json'


def select_campaign(path, allow_demo=False, selection=ACTIVE):
    path = Path(path).resolve()
    if not path.is_relative_to((ROOT/'campaigns').resolve()) or not path.is_file():
        raise ValueError('Existing save expected in campaigns/')
    campaign = Campaign(path)
    fictional = bool(campaign.config().get('fictional', False))
    if fictional and not allow_demo:
        raise ValueError('Fictional demo; select it explicitly with --demo')
    campaign.resume()
    value = {'campaign_path':path.relative_to(ROOT).as_posix(), 'kind':'demo' if fictional else 'engine'}
    selection = Path(selection)
    temporary = selection.with_name(selection.name+'.'+uuid.uuid4().hex+'.tmp')
    temporary.write_text(check_json(value)+'\n',encoding='utf-8')
    temporary.replace(selection)
    return value


def active_campaign(selection=ACTIVE):
    """Resolve the selection without generating narration or changing campaign."""
    selected = json.loads(Path(selection).read_text(encoding='utf-8'))
    if selected['campaign_path'] is None:
        return selected, None
    path = (ROOT/selected['campaign_path']).resolve()
    if not path.is_relative_to((ROOT/'campaigns').resolve()) or not path.is_file():
        raise ValueError('Active save missing or outside the campaigns/ folder')
    campaign = Campaign(path)
    fictional = bool(campaign.config().get('fictional',False))
    if selected.get('kind') != ('demo' if fictional else 'engine'):
        raise ValueError('Campaign type differs from the recorded selection')
    return selected, campaign


def resume_active(selection=ACTIVE):
    selected, campaign = active_campaign(selection)
    if campaign is None:
        return {'status':'no_active_campaign',
                'reason':'No campaign is selected: create one with `novel campaign new <novel>`.'}
    return {'status':'resume_available', 'campaign_path':selected['campaign_path'],
            'kind':selected['kind'], **campaign.resume()}
