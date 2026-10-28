"""Explicit, backed-up operations on an existing campaign, with no turn played and no model call.

`unblock` grants one more generation to the pending action once its budget is spent (two outages,
two refused answers). `configure` switches the optional creations format and the bounded automatic
recovery. Both preview by default; `apply=True` takes a backup of the save first.
"""
from contextlib import closing
import json
import sqlite3

from .bounded_resume import POLICY
from .contracts import check_json
from .maintenance import backup_before_write
from .narrative import narrative_lock
from .recovery import RecoveryBudget, exists


def _read(campaign):
    with closing(sqlite3.connect(campaign.path.resolve().as_uri() + '?mode=ro', uri=True)) as con:
        state, config = map(json.loads, con.execute('SELECT state,config FROM campaign WHERE id=1').fetchone())
        row = con.execute('SELECT payload FROM session WHERE id=1').fetchone()
        return state, config, json.loads(row[0]) if row else {}


def _plan(con):
    session = json.loads(con.execute('SELECT payload FROM session WHERE id=1').fetchone()[0])
    pending = session.get('pending_action')
    if not pending:
        return {'status': 'nothing_to_unblock', 'reason': 'No action is waiting.'}
    state = json.loads(con.execute('SELECT state FROM campaign WHERE id=1').fetchone()[0])
    if not exists(con, 'action_recovery_budget'):
        return {'status': 'nothing_to_unblock', 'request_id': pending['request_id'],
                'reason': 'This action has not used any generation yet: play it with --resume.'}
    budget = RecoveryBudget(con, pending['request_id'])
    counts, limits = budget.state(), budget.limits()
    report = {'request_id': pending['request_id'], 'state_version': state['state_version'],
              'counts': {key: counts[key] for key in ('generations', 'searches')}, 'limits': limits,
              'action': pending['text']}
    if not budget.generation_exhausted():
        return {**report, 'status': 'nothing_to_unblock',
                'reason': 'The action still has a generation left: use `novel play --resume --retry`.'}
    wanted = {'generations': counts['generations'] + 1, 'searches': limits['searches']}
    return {**report, 'status': 'preview', 'new_limits': wanted,
            'repair_id': 'extra_generation_%d' % counts['generations']}


def unblock(campaign, apply=False):
    """One more generation for the pending action; the counters and every receipt are kept."""
    # The budget helper writes its own bookkeeping: preview it on an in-memory copy, never on the save.
    with closing(sqlite3.connect(campaign.path.resolve().as_uri() + '?mode=ro', uri=True)) as source:
        with closing(sqlite3.connect(':memory:')) as con:
            source.backup(con)
            report = _plan(con)
    if report['status'] != 'preview':
        return report
    if not apply:
        return {**report, 'mutation': False, 'next': 'Add --apply to grant it (the save is backed up first).'}
    with narrative_lock(campaign):
        with backup_before_write(campaign, 'extra_generation') as backup:
            with campaign.transaction() as con:
                report = _plan(con)
                if report['status'] != 'preview':
                    return report
                budget = RecoveryBudget(con, report['request_id'])
                history = budget.state()['history']
                if any(item.get('repair_id') == report['repair_id'] for item in history):
                    return {**report, 'status': 'already_applied'}
                history.append({'repair_id': report['repair_id'], 'state_version': report['state_version'],
                                'limits': report['new_limits'], 'counts_before': report['counts'],
                                'backup': str(backup), 'query': '', 'references': [],
                                'reason': 'Explicit user request: one more generation after a failure.'})
                con.execute('UPDATE action_recovery_budget SET history=? WHERE request_id=?',
                            (check_json(history), budget.request_id))
                if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Incorrect SQLite integrity.')
    return {**report, 'status': 'granted', 'mutation': True, 'backup': str(backup), 'model_calls': 0,
            'next': 'Run `novel play --resume --retry`.'}


def configure(campaign, apply=False, creations=None, bounded_recovery=None):
    """Switch the creations format and/or the bounded recovery of an existing campaign."""
    state, config, _ = _read(campaign)
    target = dict(config)
    if creations is not None:
        target['creation_format'] = '1' if creations else None
    if bounded_recovery is not None:
        target['recovery_policy'] = POLICY if bounded_recovery else None
    report = {'status': 'preview', 'mutation': False, 'campaign_id': state['campaign_id'],
              'state_version': state['state_version'], 'creation_format': target.get('creation_format'),
              'recovery_policy': target.get('recovery_policy')}
    if target == config:
        return {**report, 'status': 'already_configured'}
    if not apply:
        return {**report, 'next': 'Add --apply to change it (the save is backed up first).'}
    with narrative_lock(campaign):
        with backup_before_write(campaign, 'configure') as backup:
            with campaign.transaction() as con:
                current = json.loads(con.execute('SELECT config FROM campaign WHERE id=1').fetchone()[0])
                if current != config:
                    raise ValueError('Campaign modified since the preview; nothing changed.')
                con.execute('UPDATE campaign SET config=? WHERE id=1', (check_json(target),))
    return {**report, 'status': 'configured', 'mutation': True, 'backup': str(backup), 'model_calls': 0}
