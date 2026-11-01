"""Encounters requested by the player: count the replies and require the encounter by the limit reply."""
from .agenda import Agenda


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
