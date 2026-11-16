"""Explicit campaign selection, independent of a chat's memory."""
import json
from pathlib import Path
import uuid

from .campaign import Campaign
from .contracts import ROOT, check_json

ACTIVE = ROOT / 'campaigns/active.json'


def select_campaign(path, allow_demo=False, selection=ACTIVE):
    path = Path(path)
    if not path.is_absolute() and (ROOT/path).is_file():
        path = ROOT/path  # campaigns/<slug>/campaign.sqlite, as printed by `novel resume`, from any folder
    path = path.resolve()
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


class ActiveCampaignError(ValueError):
    """The selection of the active campaign is missing, unreadable or points nowhere."""

    def __init__(self, reason):
        super().__init__(f'No usable active campaign ({reason}): run `novel campaign new <novel>` or '
                         '`novel resume --enable campaigns/<slug>/campaign.sqlite`.')


def read_selection(selection=ACTIVE):
    """The content of the selection file; ActiveCampaignError when it cannot be used."""
    name = Path(selection).name
    try:
        selected = json.loads(Path(selection).read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise ActiveCampaignError(f'{name} is missing') from None
    except OSError:
        raise ActiveCampaignError(f'{name} cannot be read as a file') from None
    except ValueError:
        raise ActiveCampaignError(f'{name} is not valid JSON') from None
    if (not isinstance(selected, dict) or 'campaign_path' not in selected
            or not (selected['campaign_path'] is None or isinstance(selected['campaign_path'], str))):
        raise ActiveCampaignError(f'{name} does not name a campaign')
    return selected


def active_campaign(selection=ACTIVE):
    """Resolve the selection without generating narration or changing campaign."""
    selected = read_selection(selection)
    if selected['campaign_path'] is None:
        return selected, None
    try:
        path = (ROOT/selected['campaign_path']).resolve()
        found = path.is_relative_to((ROOT/'campaigns').resolve()) and path.is_file()
    except (OSError, ValueError):
        found = False
    if not found:
        raise ActiveCampaignError(f"the save {selected['campaign_path']} is missing or outside the campaigns/ folder")
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
