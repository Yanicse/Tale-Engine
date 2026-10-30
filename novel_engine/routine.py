"""Local handling of ellipses, with the narrator's answers in the chat."""
from copy import deepcopy
from contextlib import closing
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import uuid

from .agenda import date
from .campaign import Campaign
from .contracts import check_json
from .narrative import NarrativeLoop, narrative_lock
from .narration_codex import response_schema
from .planner import WorldPlanner, planner_schema, instructions_for
from .prompts import load as load_prompt

DAY = 1440
MAX_DAYS = 365
LEVELS = ('info', 'alert', 'critical')
CATEGORIES = ('base_threat', 'harm_to_player_or_relatives', 'key_npc', 'arc_in_crisis',
              'message_for_player', 'unique_resource', 'extreme_roll', 'other')
# Long routine: adaptive step (up to the next real deadline), short daily bulletin,
# review periods and stops by level of importance.
DEFAULT_POLICY = {'points_per_step': None, 'period_days': 7, 'pause_period': False,
                  'stop_on': ['critical'], 'alert_days': 3, 'arcs_review_days': 2,
                  'bulletins': True, 'signals': True}
# Files without a recorded policy: old behavior, one step per 6 pm point.
LEGACY_POLICY = {'points_per_step': 1, 'period_days': None, 'pause_period': False,
                 'stop_on': ['critical'], 'alert_days': 0, 'arcs_review_days': 2,
                 'bulletins': False, 'signals': False}
DEFAULT_TOPICS = ['factions and their decisions', 'inhabitants and relatives', 'projects and commitments',
                  'threats and world initiatives']
DEFAULT_DELEGATION = load_prompt('routine.default_delegation')
GUIDANCE = load_prompt('routine.guidance')


def policy_text(policy):
    """Long routine rules attached to the instructions of a step."""
    lines = ['LONG ROUTINE RULES']
    if policy['bulletins']:
        lines.append(('Short daily bulletin: for each daily point (6 pm) crossed, write in narration a line '
                      'starting with its marker, for example "D12: nothing important reaches the player '
                      'character." A calm day fits in one line; a busy day in two or three sentences. A '
                      'finding creates no news: invent nothing to fill a day.'))
    if policy['signals']:
        lines.append((('Signals: fill routine_signals (list, empty if nothing). Declare each important fact '
                       'with level (info, alert, critical), category, world_minute and reason. Critical: '
                       'serious threat against the base of the player character, harm to the player character '
                       'or a relative, death or disappearance of a key NPC, arc in crisis, message or visitor '
                       'addressed to the player character, threatened unique resource, extreme roll. A critical '
                       'signal (or one of a stopping level: ')) + ', '.join(policy['stop_on']) + ((') ends the step at its world_minute and hands control back to the player; no event '
                                                                                                  'after it. An alert brings the steps back to one day for ')) + str(policy['alert_days']) + ' days. An info is only logged.')
    if policy['period_days']:
        lines.append('Steps are grouped into periods of ' + str(policy['period_days']) + ' days; a review is recorded at the end of each period.')
    return '\n'.join(lines)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, value):
    """Atomic replacement of a private file, never of a SQLite database."""
    path = Path(path)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(check_json(value) + '\n', encoding='utf-8')
    temp.replace(path)


def read_state(campaign):
    """Read-only, including for the preview and the opening of a routine."""
    if not campaign.path.is_file():
        raise ValueError('Existing save required.')
    with closing(sqlite3.connect(campaign.path.resolve().as_uri() + '?mode=ro', uri=True)) as con:
        return json.loads(con.execute('SELECT state FROM campaign WHERE id=1').fetchone()[0])


def preview(campaign, days=7):
    if type(days) is not int or not 1 <= days <= MAX_DAYS:
        raise ValueError('Whole duration from 1 to %d days expected.' % MAX_DAYS)
    state = read_state(campaign)
    start = state['world_minute']
    return {'campaign_id': state['campaign_id'], 'state_version': state['state_version'],
            'start_minute': start, 'end_minute': start + days * DAY,
            'start': date(start), 'end': date(start + days * DAY), 'days': days}


def normalize_policy(policy=None):
    merged = {**DEFAULT_POLICY, **(policy or {})}
    unknown = set(merged) - set(DEFAULT_POLICY)
    if unknown:
        raise ValueError('Unknown routine option: ' + ', '.join(sorted(unknown)))
    points = merged['points_per_step']
    if points is not None and (type(points) is not int or not 1 <= points <= MAX_DAYS):
        raise ValueError('points_per_step: positive integer or absent.')
    period = merged['period_days']
    if period is not None and (type(period) is not int or not 1 <= period <= MAX_DAYS):
        raise ValueError('period_days: integer from 1 to %d or absent.' % MAX_DAYS)
    if type(merged['alert_days']) is not int or not 0 <= merged['alert_days'] <= 30:
        raise ValueError('alert_days: integer from 0 to 30.')
    if type(merged['arcs_review_days']) is not int or not 2 <= merged['arcs_review_days'] <= 30:
        raise ValueError('arcs_review_days: integer from 2 to 30.')
    stops = sorted(set(merged['stop_on']) | {'critical'}, key=LEVELS.index) \
        if set(merged['stop_on']) <= set(LEVELS) else None
    if stops is None:
        raise ValueError('stop_on: values among ' + ', '.join(LEVELS) + '.')
    merged['stop_on'] = [s for s in stops if s != 'info']
    for key in ('pause_period', 'bulletins', 'signals'):
        merged[key] = bool(merged[key])
    return merged


def daily_points(start, end):
    """Daily 6 pm points in ]start, end], with their Dn marker."""
    point = start // DAY * DAY + 18 * 60
    if point <= start:
        point += DAY
    points = []
    while point <= end:
        points.append({'marker': 'D%d' % (point // DAY), 'date': date(point), 'minute': point})
        point += DAY
    return points


def turn_limit(prepared, target, max_points=None, period_end=None, fine_until=None):
    """Bound of a step: the next real deadline, never the plain 6 pm point.

    max_points limits the number of daily points per step (1: old behavior);
    fine_until imposes one step per day while an alert is active."""
    start = prepared['state']['world_minute']
    first = daily_points(start, start + DAY)[0]['minute']
    agenda = prepared['world_agenda']
    limits = [target]
    if period_end is not None:
        limits.append(period_end)
    if max_points is not None:
        limits.append(first + (max_points - 1) * DAY)
    if fine_until is not None and start < fine_until:
        limits.append(first)
    limits.extend(agenda[k] for k in ('arcs_review_horizon', 'max_horizon_if_limited')
                  if agenda.get(k) is not None)
    limits.extend(max(start, e['due_minute']) for e in agenda['events'] if e['interrupt'])
    return min(limits)


class NoRemote:
    def generate(self, prompt):
        raise RuntimeError('The routine is run in the chat; no remote generator available.')


class Routine:
    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.manifest = self.folder / 'session.json'
        self.data = read_json(self.manifest)
        if self.data.get('version') != 1:
            raise ValueError('Incompatible routine folder version.')
        self.campaign = Campaign(self.data['campaign'])
        if not self.campaign.path.is_file():
            raise ValueError('The save of this routine folder is missing; no automatic database creation.')

    @classmethod
    def create(cls, folder, campaign, days=7, objective='', topics=None, delegation=DEFAULT_DELEGATION,
               policy=None):
        plan = preview(campaign, days)
        policy = normalize_policy(policy)
        folder = Path(folder).resolve()
        folder.mkdir(parents=True, exist_ok=False)
        data = {'version': 1, 'id': uuid.uuid4().hex, 'campaign': str(campaign.path.resolve()),
                **plan, 'goal': objective or 'Follow the routine and the news day by day.',
                'subjects': list(topics or DEFAULT_TOPICS), 'delegation': delegation,
                'status': 'ready', 'expected_state_version': plan['state_version'],
                'policy': policy, 'alert_until': None, 'periods': [],
                'index': 0, 'active': None, 'steps': []}
        write_json(folder / 'session.json', data)
        return cls(folder)

    @property
    def policy(self):
        return self.data.get('policy') or LEGACY_POLICY

    def _period(self, minute):
        days = self.policy['period_days']
        return (minute - self.data['start_minute']) // (days * DAY) if days else 0

    def _period_end(self, minute):
        days = self.policy['period_days']
        return self.data['start_minute'] + (self._period(minute) + 1) * days * DAY if days else None

    def review(self, period, end):
        """Compact review of a period, reread in the accepted receipts of the folder."""
        steps = [s for s in self.data['steps'] if s['phase'] == 'turn' and s.get('period') == period]
        narrations = [read_json(self.folder / s['file'] / 'result.json').get('narration', '')
                      for s in steps]
        signals = [{**g, 'date': date(g['world_minute'])} for s in steps for g in s.get('signals', [])]
        return {'period': period + 1, 'end': date(end), 'steps': len(steps),
                'narration': '\n'.join(n for n in narrations if n), 'signals': signals}

    def _save(self):
        write_json(self.manifest, self.data)

    def _reload(self):
        self.data = read_json(self.manifest)
        if Path(self.data['campaign']).resolve() != self.campaign.path.resolve():
            raise ValueError('The folder changed campaign; open the routine again explicitly.')
        if not self.campaign.path.is_file():
            raise ValueError('The save of this routine folder is missing; no automatic database creation.')

    def _action(self):
        return (f"Routine of {self.data['days']} days (folder {self.data['id']}) until {self.data['end']} "
                f"(minute {self.data['end_minute']}, fixed end). "
                f"Goal: {self.data['goal']} Topics: {'; '.join(self.data['subjects'])}. "
                f"Delegation: {self.data['delegation']} Narrator in the chat, no remote call.")

    def status(self):
        self._reload()
        state = read_state(self.campaign)
        return {'status': self.data['status'], 'folder': str(self.folder),
                'current_date': date(state['world_minute']), 'end': self.data['end'],
                'minutes_left': max(0, self.data['end_minute'] - state['world_minute']),
                'recorded_steps': len(self.data['steps']), 'active': self.data['active'],
                'closed_periods': len(self.data.get('periods', [])),
                'alert_until': date(self.data['alert_until']) if self.data.get('alert_until') else None,
                'external_drift': state['state_version'] != self.data['expected_state_version']}

    def _view(self, folder):
        work = read_json(folder / 'work.json')
        return {'status': 'expected_answer', 'phase': work['phase'], 'step': str(folder),
                'packet': str(folder / 'packet.json'), 'instructions': str(folder / 'instructions.txt'),
                'schema': str(folder / 'schema.json'), 'end_minute_step': work.get('bound'),
                'model': str(folder / 'model_answer.json'),
                'private_archives': str(folder / 'work.json'),
                'end_routine': self.data['end'], 'remote_calls': 0}

    def prepare(self, resume=True):
        with narrative_lock(self.campaign):
            self._reload()
            if self.data['status'] in ('finished', 'decision_required', 'to_examine'):
                return self.status()
            if self.data['status'] == 'period_finished' and not resume:
                return self.status()
            active = self.data['active']
            if active and active.get('file'):
                return self._view(self.folder / active['file'])
            state = read_state(self.campaign)
            if state['state_version'] != self.data['expected_state_version']:
                raise ValueError('The campaign evolved outside this file; examine the resume before continuing.')
            saved = self.campaign.resume()
            pending = saved['session']['pending_action']
            if pending and (not active or pending['request_id'] != active['request_id']):
                raise ValueError('Another action is already waiting; the routine does not replace it.')
            if not active:
                active = {'request_id': uuid.uuid5(uuid.NAMESPACE_URL,
                    self.data['id'] + ':' + str(self.data['index'])).hex, 'file': None}
                self.data['active'] = active
                self._save()  # Stable identifier even after an interruption before input.
            if pending is None:
                self.campaign.begin_action(self._action(), active['request_id'])
            prepared = self.campaign.prepare_turn(narrative_memory=True)
            if prepared['status'] != 'ready':
                raise ValueError('Action already rejected; explicit re-evaluation needed through the usual path.')
            with self.campaign.transaction() as con:
                planner = WorldPlanner(con)
                reasons = planner.reasons(prepared['context']['player_action']) if planner.exists() else []
                packet = planner.packet(prepared, reasons) if reasons else None
            if packet is not None and self.policy['arcs_review_days'] != 2:
                arcs = packet.get('chronicle', {}).get('arcs_registry')
                if arcs is not None:
                    arcs['max_review_delay'] = self.policy['arcs_review_days'] * DAY
            folder = self.folder / f"step_{self.data['index']:04d}"
            folder.mkdir(exist_ok=True)
            if packet is not None:
                phase = 'world'
                schema = planner_schema(packet)
                instructions = instructions_for(packet)
                bound = None
            else:
                phase = 'turn'
                loop = NarrativeLoop(self.campaign, NoRemote(), word_target=600)
                prompt = loop.build_prompt(prepared)
                instructions, raw = prompt.rsplit('\nCURRENT TURN\n', 1)
                packet = json.loads(raw)
                policy = self.policy
                now = prepared['state']['world_minute']
                bound = turn_limit(prepared, self.data['end_minute'], policy['points_per_step'],
                                   self._period_end(now), self.data.get('alert_until'))
                packet['routine'] = {'goal': self.data['goal'], 'subjects': self.data['subjects'],
                    'delegation': self.data['delegation'], 'end_minute': self.data['end_minute'],
                    'bound_turn': bound, 'instruction': 'Go to this bound unless there is a real interruption; no overrun.'}
                if policy['bulletins']:
                    packet['routine']['daily_points'] = [{k: p[k] for k in ('marker', 'date')}
                                                              for p in daily_points(now, bound)]
                if policy['signals']:
                    packet['routine'].update(signal_levels=list(LEVELS), signal_categories=list(CATEGORIES),
                        stop_on=policy['stop_on'], alert_in_progress=bool(self.data.get('alert_until')
                                                                           and now < self.data['alert_until']))
                version = '1.3' if prepared['campaign_rules'].get('mechanics') else '1.2'
                schema = response_schema(version=version, references=packet['allowed_references'],
                    memory_references=packet.get('memory_references'),
                    creations=prepared['campaign_rules'].get('creation_format') == '1')
                if policy['signals'] and isinstance(schema.get('properties'), dict):
                    schema['properties']['routine_signals'] = {'type': 'array', 'maxItems': 12, 'items': {
                        'type': 'object', 'additionalProperties': False,
                        'required': ['level', 'category', 'world_minute', 'reason'],
                        'properties': {'level': {'type': 'string', 'enum': list(LEVELS)},
                                       'category': {'type': 'string', 'enum': list(CATEGORIES)},
                                       'world_minute': {'type': 'integer', 'minimum': 0},
                                       'reason': {'type': 'string', 'minLength': 10, 'maxLength': 400}}}}
            work = {'phase': phase, 'prepared': prepared, 'packet': packet, 'bound': bound,
                    'review_id': 'routine:' + self.data['id'] + ':' + str(self.data['index'])}
            write_json(folder / 'work.json', work)
            write_json(folder / 'packet.json', packet)
            write_json(folder / 'schema.json', schema)
            if phase == 'world':
                model = {'planner_schema_version': '2.0', 'player_summary': None,
                         'old_synthesis': None, 'chronicle': None}
                for source, key in (('exchanges_to_summarize', 'player_summary'),
                                    ('summaries_to_consolidate', 'old_synthesis')):
                    if packet[source]:
                        model[key] = {'text': '', 'references': [e['reference'] for e in packet[source]]}
                if 'chronicle' in packet:
                    model['chronicle'] = {'clocks': [], 'manifestations': []}
                    if 'arcs_registry' in packet['chronicle']:
                        model['chronicle']['arcs'] = []
            else:
                model = {'narrative_schema_version': version, 'narration': '', 'events': [],
                         'blocking_questions': [], 'memory_request': None, 'memory_updates': [],
                         'agenda_updates': [], 'continuation': None, 'roll_checks': []}
                if prepared['campaign_rules'].get('creation_format') == '1':
                    model.update(creation_schema_version='1', creation_updates=[])
                if self.policy['signals']:
                    model['routine_signals'] = []
            write_json(folder / 'model_answer.json', model)
            (folder / 'instructions.txt').write_text(instructions + '\n\n' + GUIDANCE + '\n' +
                policy_text(self.policy) + ('\n\n'
                                            'ROUTINE GOAL\n') + self._action(), encoding='utf-8')
            active['file'] = folder.name
            self.data['status'] = 'in_progress'
            self._save()
            return self._view(folder)

    def apply(self, response):
        """A single packet, never a generation loop or a silent fix."""
        response = deepcopy(response)
        if chr(0x2014) in check_json(response):
            raise ValueError('The U+2014 character is forbidden.')
        with narrative_lock(self.campaign):
            self._reload()
            active = self.data['active']
            if not active or not active.get('file'):
                if self.data['steps']:
                    previous = self.folder / self.data['steps'][-1]['file']
                    if read_json(previous / 'answer.json') == response:
                        result = read_json(previous / 'result.json')
                        return {k: v for k, v in result.items() if k != 'applied_events'}
                raise ValueError('Prepare a step before providing its answer.')
            folder = self.folder / active['file']
            work = read_json(folder / 'work.json')
            received = folder / 'answer.json'
            if received.exists() and read_json(received) != response:
                raise ValueError('An answer was already received for this step; resume this receipt without replacing it.')
            signals = []
            if work['phase'] == 'turn':
                signals = self._check_routine_fields(work, response)  # Before any receipt: nothing is consumed.
            write_json(received, response)  # Preservation before any validation or game write.
            prepared = work['prepared']
            if work['phase'] == 'world':
                result = self._apply_world(work, response)
            else:
                core = {k: v for k, v in response.items() if k != 'routine_signals'}
                result = self._apply_turn(work, core)
            write_json(folder / 'result.json', result)
            if result['status'] not in ('recorded', 'question', 'recorded_world'):
                return result
            start = prepared['state']['world_minute']
            entry = {'file': folder.name, 'phase': work['phase'],
                     'request_id': active['request_id'], 'status': result['status']}
            if work['phase'] == 'turn':
                entry.update(period=self._period(start), start_minute=start,
                             end_minute=result['world_minute_after'], signals=signals)
            self.data['steps'].append(entry)
            self.data['index'] += 1
            self.data['expected_state_version'] = result['state_version_after']
            extra = {}
            if work['phase'] == 'world':
                self.data['active'] = {'request_id': active['request_id'], 'file': None}
            else:
                self.data['active'] = None
                end = result['world_minute_after']
                interrupted = any(e['interrupt'] and max(start, e['due_minute']) <= end
                    for e in prepared['world_agenda']['events'])
                stops = [g for g in signals if g['level'] in self.policy['stop_on']]
                alerts = [g for g in signals if g['level'] == 'alert']
                if alerts and self.policy['alert_days']:
                    self.data['alert_until'] = max(self.data.get('alert_until') or 0,
                                                    end + self.policy['alert_days'] * DAY)
                if stops:
                    extra['routine_stop'] = [{**g, 'date': date(g['world_minute'])} for g in stops]
                if result['status'] == 'question' or interrupted or stops:
                    self.data['status'] = 'decision_required'
                elif end >= self.data['end_minute']:
                    self.data['status'] = 'finished'
                elif end <= start:
                    self.data['status'] = 'to_examine'
                if end > start and self.policy['period_days'] and (
                        self._period(end) > self._period(start) or end >= self.data['end_minute']):
                    review = self.review(self._period(start), end)
                    self.data['periods'].append(review)
                    extra['review_period'] = review
                    extra['resume_new_chat'] = ('novel routine prepare --folder "' + str(self.folder) + '"')
                    if self.policy['pause_period'] and self.data['status'] == 'in_progress':
                        self.data['status'] = 'period_finished'
            self._save()
            return {**{k: v for k, v in result.items() if k != 'applied_events'}, **extra}

    def _check_routine_fields(self, work, response):
        """Signals and daily bulletin, checked before any recording of the receipt."""
        policy = self.policy
        signals = response.get('routine_signals')
        if signals is None:
            if policy['signals']:
                raise ValueError('routine_signals is required (empty list if nothing to report).')
            return []
        start = work['prepared']['state']['world_minute']
        if not isinstance(signals, list) or len(signals) > 12:
            raise ValueError('routine_signals: at most 12 signals.')
        for g in signals:
            if not isinstance(g, dict) or set(g) != {'level', 'category', 'world_minute', 'reason'}:
                raise ValueError('Invalid routine signal: level, category, world_minute and reason.')
            if g['level'] not in LEVELS or g['category'] not in CATEGORIES:
                raise ValueError('Routine signal: unknown level or category.')
            if type(g['world_minute']) is not int or not start <= g['world_minute'] <= work['bound']:
                raise ValueError('Routine signal outside the period of this step.')
            if not isinstance(g['reason'], str) or not 10 <= len(g['reason']) <= 400:
                raise ValueError('Routine signal: reason of 10 to 400 characters.')
        minutes = [e.get('world_minute') for e in response.get('events', []) if isinstance(e, dict)]
        end = max([m for m in minutes if type(m) is int] or [start])
        stops = [g['world_minute'] for g in signals if g['level'] in policy['stop_on']]
        if stops and end > min(stops):
            raise ValueError('A stop signal ends the step at its world_minute: no event after it.')
        if policy['bulletins']:
            text = response.get('narration') or ''
            missing = [p['marker'] for p in daily_points(start, end)
                       if not re.search(r'(?<![0-9A-Za-z])' + p['marker'] + r'(?![0-9])', text)]
            if missing:
                raise ValueError('Daily bulletin missing from narration: ' + ', '.join(missing) +
                                 ' (one short line per day, starting with the marker).')
        return signals

    def _apply_world(self, work, response):
        identifier = work['review_id']
        packet = work['packet']
        digest = hashlib.sha256(check_json(response).encode('utf-8')).hexdigest()
        with self.campaign.transaction() as con:
            if self.campaign._state(con)['state_version'] != work['prepared']['state']['state_version']:
                raise ValueError('The campaign evolved during the chronicle; no application.')
            pending = self.campaign._session(con)['pending_action']
            if (pending or {}).get('request_id') != work['prepared']['model_proposal']['request_id']:
                raise ValueError('Different pending action during the chronicle; no application.')
            old = con.execute('SELECT status,response FROM planner_reviews WHERE id=?', (identifier,)).fetchone()
            if old and json.loads(old['response']) != response:
                raise ValueError('Different chronicle receipt; overwriting forbidden.')
            if not old or old['status'] != 'applied':
                con.execute('INSERT OR REPLACE INTO planner_reviews VALUES (?,?,?,?,?,?)',
                    (identifier, 'prepare', check_json(packet), check_json(response),
                     check_json({'model_calls': 0, 'source': 'narrator_chat', 'sha256': digest}), ''))
                WorldPlanner(con).apply_review(identifier, packet, response)
        state = read_state(self.campaign)
        return {'status': 'recorded_world', 'state_version_after': state['state_version'],
                'world_minute_after': state['world_minute'], 'narration': '', 'remote_calls': 0}

    def correct(self):
        """Explicit reopening of a refused answer, with no new automatic generation."""
        with narrative_lock(self.campaign):
            self._reload()
            active = self.data['active']
            if not active or not active.get('file'):
                raise ValueError('No active answer to fix.')
            folder = self.folder / active['file']
            work = read_json(folder / 'work.json')
            received = folder / 'answer.json'
            if not received.exists():
                raise ValueError('No received answer to fix.')
            if work['phase'] == 'world':
                with self.campaign.transaction() as con:
                    done = con.execute("SELECT 1 FROM planner_reviews WHERE id=? AND status='applied'",
                                       (work['review_id'],)).fetchone()
                if done:
                    raise ValueError('Chronicle already recorded; reuse its receipt.')
            else:
                result = self.campaign.result(active['request_id'])
                if result and result['status'] != 'rejected':
                    raise ValueError('Turn already accepted; reuse its receipt.')
                if result:
                    prepared = self.campaign.prepare_turn(reevaluate=True, narrative_memory=True)
                    active['request_id'] = prepared['model_proposal']['request_id']
                else:
                    pending = self.campaign.resume()['session']['pending_action']
                    if (pending or {}).get('request_id') != active['request_id']:
                        raise ValueError('Another action is waiting; the fix does not replace it.')
            received.replace(folder / ('answer_refused' + uuid.uuid4().hex + '.json'))
            # Explicit recomputation of sources, bounds and revisions, without reusing an obsolete packet.
            active['file'] = None
            self.data['index'] += 1
            self.data['expected_state_version'] = read_state(self.campaign)['state_version']
            self._save()
            return {'status': 'allowed_correction', 'remote_calls': 0,
                    'next': 'prepare then provide a fixed answer; no accepted answer modified.'}

    def _apply_turn(self, work, response):
        prepared = work['prepared']
        request_id = prepared['model_proposal']['request_id']
        existing = self.campaign.result(request_id)
        if existing:
            return existing
        saved = self.campaign.resume()
        if (saved['state_version'] != prepared['state']['state_version'] or
            (saved['session']['pending_action'] or {}).get('request_id') != request_id):
            raise ValueError('Obsolete step; state or pending action modified, no application.')
        loop = NarrativeLoop(self.campaign, NoRemote(), word_target=600)
        # Common conversion: identifiers, sources, agenda, memory, creations, mechanics.
        proposal = loop._proposal(prepared, response, packet=deepcopy(work['packet']))
        if any(e['world_minute'] > work['bound'] for e in proposal['events']):
            raise ValueError('The answer exceeds the bound of this routine step.')
        return self.campaign.apply(proposal, agenda_changes=loop._agenda_changes,
            memory_updates=loop._memory_updates, creation_updates=loop._creation_updates,
            location_reminders=loop._location_reminders,
            expected_versions=prepared['state']['versions'])

    def report(self):
        """Only the narrations actually accepted, reread from SQLite."""
        self._reload()
        narrations = []
        for step in self.data['steps']:
            if step['phase'] != 'turn':
                continue
            result = self.campaign.result(step['request_id'])
            if result and result['status'] in ('recorded', 'question'):
                narrations.append(result['narration'])
        return {'status': self.data['status'], 'narration': '\n\n'.join(narrations),
                'narration_count': len(narrations)}
