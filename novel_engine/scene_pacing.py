"""Staging deadlines requested by the player, with no turn on reading."""
from .agenda import Agenda


def packet(con, config):
    output = []
    for directive in config.get('scene_pacing', []):
        entry = Agenda(con).get(directive['entry_id'])
        if entry is None or entry['status'] in ('done', 'finished'):
            continue
        count = con.execute("SELECT COUNT(*) FROM exchanges WHERE role='narrator' AND seq>?",
                            (directive['after_seq'],)).fetchone()[0]
        output.append({**directive, 'answers_since_request': count,
                       'answer_rank_to_produce': count + 1,
                       'remaining_before_limit': max(0, directive['max_replies'] - count),
                       'manifestation_date': entry['due_minute'],
                       'instruction': ('The requested encounter must be played at the latest by the limit reply, during the '
                                    'current scene. A preparation, an indirect message or a postponement does not replace '
                                    'presence and a chance for dialogue. The deadline marks the nearby manifestation point: '
                                    'advance fictional time to it at the latest within this limit reply, then stop before '
                                    'deciding the reaction of the player character. Technical exchanges and rejected '
                                    'attempts are not played replies.')})
    return output


def validate(directives, proposal, agenda_updates):
    for directive in directives:
        update = next((u for u in agenda_updates if u['entry_id'] == directive['entry_id']), None)
        if update and update['status'] in ('postponed', 'cancelled'):
            raise ValueError('A short-term requested encounter cannot be replaced by a postponement or a cancellation.')
        if directive['answer_rank_to_produce'] < directive['max_replies']:
            continue
        if not update or update['status'] not in ('done', 'finished'):
            raise ValueError('The three-reply limit requires the requested encounter to manifest.')
        index = update['outcome_event_index']
        if type(index) is not int or not 0 <= index < len(proposal['events']) or 'player' not in proposal['events'][index]['audience']:
            raise ValueError('The encounter due now must be perceivable to the player character.')
