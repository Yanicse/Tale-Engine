"""Pacing: encounters requested by the player, and recurring news during quiet stretches of the story."""
import json

from .agenda import ACTIVE, QUIET_NEWS_PREFIX, Agenda

QUIET_NEWS_REFERENCE = 'pacing:quiet_news'
NEWS_GUIDANCE = ('Show each quiet news entry at its fictional time, as a development that credibly reaches the player '
                 'character. Use an outcome event the player perceives, with the agenda reference in its causes, and '
                 'keep the agenda entry private. News that can wait leaves the player free to continue and can be told '
                 'within a longer activity. If a fight or a separate urgent interruption has already taken over, leave '
                 'the entry alone: the engine resets it.')
NEWS_REASON = ('Bring one new report, consistent with the established facts, to the player character through a '
               'credible person, message or observable sign. Use the most relevant unresolved NPC, faction or '
               'consequence of play; the clock itself is not a cause. ')
NEWS_URGENT = ('A concrete development needs a timely choice. Show what may happen if the player character waits, '
               'then stop for the decision: act, delegate or accept the risk. A fight is only one possibility.')
NEWS_CALM = ('This news can wait. Let the player character receive it without being committed to act, and let the '
             'current activity continue if that is what the player chose.')


def clean_quiet_news(value):
    """Pack option `quiet_news`, reduced to valid values; None when it is absent or unusable."""
    if not isinstance(value, dict):
        return None
    interval = value.get('interval_minutes')
    if type(interval) is not int or not 30 <= interval <= 43200:
        return None
    every = value.get('urgent_every', 3)
    policy = {'interval_minutes': interval, 'urgent_every': every if type(every) is int and 0 <= every <= 100 else 3}
    instruction = value.get('instruction')
    if isinstance(instruction, str) and instruction.strip():
        policy['instruction'] = ' '.join(instruction.split())[:400]
    return policy


def quiet_guidance(con):
    """Standing instruction for the narrator when the campaign schedules quiet news."""
    row = con.execute('SELECT config FROM campaign WHERE id=1').fetchone()
    policy = json.loads(row[0]).get('quiet_news') if row else None
    if not policy:
        return None
    return {'news': NEWS_GUIDANCE, **({'pack_instruction': policy['instruction']} if policy.get('instruction') else {})}


def combat_starts(event):
    return (any(op['kind'] == 'combat_start' for op in event.get('mechanical_operations', [])) or
            any(change['path'] == '/resources/player_combat_active' and change.get('value') == 1
                for change in event.get('changes', [])))


def quiet_news_overtaken(state, events, updates, minute, continuation=None):
    """A fight or a real alarm takes precedence over the end of a quiet stretch."""
    news_indexes = {u['outcome_event_index'] for u in updates if u['entry_id'].startswith(QUIET_NEWS_PREFIX)}
    index = (continuation or {}).get('event_index')
    other_interruption = (type(index) is int and 0 <= index < len(events) and index not in news_indexes
                          and events[index]['world_minute'] <= minute)
    if state['resources'].get('player_combat_active') or other_interruption:
        return True
    if any(combat_starts(e) for e in events if e['world_minute'] <= minute):
        return True
    for update in updates:
        index = update['outcome_event_index']
        if (update['interrupt'] and not update['entry_id'].startswith(QUIET_NEWS_PREFIX)
                and update['status'] in ('done', 'finished') and type(index) is int
                and 0 <= index < len(events) and events[index]['world_minute'] <= minute):
            return True
    return False


def record_quiet_news(agenda, config, before, after, result, changes):
    """Schedule the next quiet news after a played turn, in the same transaction as its clock and agenda changes.

    One entry is pending at a time, `interval_minutes` after the turn that scheduled it. A fight or another
    interruption that takes over cancels it; the next one is scheduled once the fight has ended."""
    policy = config['quiet_news']
    minute = after['world_minute']
    request_id = result['request_id']
    size = len(QUIET_NEWS_PREFIX) + 1
    row = agenda.con.execute('SELECT entry_id FROM world_agenda WHERE entry_id GLOB ? '
                             'ORDER BY CAST(substr(entry_id,?) AS INTEGER) DESC LIMIT 1',
                             (QUIET_NEWS_PREFIX + '*', size)).fetchone()
    previous = agenda.get(row[0]) if row else None
    in_combat = after['resources'].get('player_combat_active')
    if previous and previous['status'] in ACTIVE:
        if not (in_combat or quiet_news_overtaken(before, result['applied_events'], changes['updates'], minute,
                                                  changes['continuation'])):
            return
        cancelled = {k: v for k, v in previous.items() if k != 'revision'}
        cancelled.update(status='cancelled', reason='The quiet stretch ended in play before this news was needed.',
                         expected_revision=previous['revision'], outcome_event_index=None)
        agenda._put(cancelled, request_id, minute, previous['revision'] + 1)
    if in_combat:
        return
    number = int(previous['entry_id'].rsplit(':', 1)[1]) + 1 if previous else 1
    delivered = agenda.con.execute("SELECT COUNT(*) FROM world_agenda WHERE entry_id GLOB ? "
                                   "AND status IN ('done','finished')", (QUIET_NEWS_PREFIX + '*',)).fetchone()[0]
    every = policy.get('urgent_every', 3)
    urgent = bool(every) and (delivered + 1) % every == 0
    reason = NEWS_REASON + (policy['instruction'] + ' ' if policy.get('instruction') else '')
    agenda._put({'entry_id': QUIET_NEWS_PREFIX + str(number), 'expected_revision': -1,
                 'title': 'News that calls for a timely choice' if urgent else 'A development reaches the player character',
                 'actor': 'A relevant established actor or messenger',
                 'location': 'Where the player character can receive it',
                 'kind': 'risk', 'due_minute': minute + policy['interval_minutes'], 'conditions': [],
                 'references': [QUIET_NEWS_REFERENCE], 'audience': ['narrator'], 'interrupt': urgent,
                 'status': 'planned', 'reason': reason + (NEWS_URGENT if urgent else NEWS_CALM),
                 'outcome_event_index': None}, request_id, minute)


def packet(con, config):
    output = []
    for directive in config.get('scene_pacing', []):
        entry = Agenda(con).get(directive['entry_id'])
        if entry is None or entry['status'] in ('done', 'finished'):
            continue
        count = con.execute("SELECT COUNT(*) FROM exchanges WHERE role='narrator' AND seq>?",
                            (directive['after_seq'],)).fetchone()[0]
        output.append({**directive, 'replies_since_request': count,
                       'reply_number': count + 1,
                       'remaining_before_limit': max(0, directive['max_replies'] - count),
                       'encounter_due_minute': entry['due_minute'],
                       'instruction': ('The requested encounter must be played no later than the limit reply, during the '
                                    'current scene. A preparation, an indirect message or a postponement does not replace '
                                    'the encounter itself, with a chance for dialogue. The deadline is the moment the '
                                    'encounter must happen: advance fictional time to it no later than this limit reply, '
                                    'then stop before deciding the reaction of the player character. Technical exchanges '
                                    'and rejected attempts are not played replies.')})
    return output


def validate(directives, proposal, agenda_updates):
    for directive in directives:
        update = next((u for u in agenda_updates if u['entry_id'] == directive['entry_id']), None)
        if update and update['status'] in ('postponed', 'cancelled'):
            raise ValueError('A short-term requested encounter cannot be replaced by a postponement or a cancellation.')
        if directive['reply_number'] < directive['max_replies']:
            continue
        if not update or update['status'] not in ('done', 'finished'):
            raise ValueError(f"The limit of {directive['max_replies']} replies requires the requested encounter to take place.")
        index = update['outcome_event_index']
        if type(index) is not int or not 0 <= index < len(proposal['events']) or 'player' not in proposal['events'][index]['audience']:
            raise ValueError('The encounter due now must be perceivable to the player character.')
