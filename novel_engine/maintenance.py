"""Explicit maintenance entry points; SQLite preview strictly read-only."""
from contextlib import closing, contextmanager
import argparse
import json
from pathlib import Path
import sqlite3
import uuid

from .campaign import Campaign


def parser(description):
    result = argparse.ArgumentParser(description=description)
    result.add_argument('--campaign', required=True, help='Explicit path of the existing save')
    result.add_argument('--apply', action='store_true', help='Apply the maintenance with a prior backup')
    return result


def execute(args, operation, apply):
    path = Path(args.campaign).resolve()
    campaign = Campaign(path)  # Refuse the paths of the corpus and the canon.
    if not path.is_file():
        raise ValueError('Existing save expected; no automatic creation.')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as con:
        row = con.execute('SELECT state,config FROM campaign WHERE id=1').fetchone()
        if row is None:
            raise ValueError('Campaign not initialized.')
        state, config = map(json.loads, row)
    if not args.apply:
        return {'status': 'preview', 'mutation': False, 'operation': operation,
                'campaign_path': str(path), 'campaign_id': state['campaign_id'],
                'state_version': state['state_version'], 'versions': state['versions'],
                'description': 'Target checked read-only. Business compatibility will be checked on application.'}
    return apply(campaign)


@contextmanager
def backup_before_write(campaign, operation):
    """To be called under narrative_lock, before the write transaction is opened."""
    folder = campaign.path.parent / 'saves'
    folder.mkdir(exist_ok=True)
    target = folder / ('before_' + operation + '_' + uuid.uuid4().hex + '.sqlite')
    with closing(sqlite3.connect(campaign.path.resolve().as_uri() + '?mode=ro', uri=True)) as source:
        with closing(sqlite3.connect(target)) as destination:
            source.backup(destination)
    yield target
