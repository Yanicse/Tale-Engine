"""Public reading (player audience) of a campaign, never writing.

Each request opens its own `mode=ro` connection and only calls reading functions:
neither `Campaign.transaction()` (write lock) nor `world_agenda()` (agreement synchronization).
Nothing that the narrator keeps private comes out of here.
"""
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3

from . import schema
from .agenda import Agenda, date
from .abilities import packet as skills_packet
from .creations import CreationRegistry
from .planner import WorldPlanner
from .possessions import packet as possessions_packet

PAGE = 20
SEARCH_LIMIT = 50
ROLES = ('player', 'narrator')


def _clean(value):
    """Remove every canon:* reference (private) from the returned values."""
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item) for item in value
                if not (isinstance(item, str) and item.startswith('canon:'))]
    return value


class Reader:
    def __init__(self, path):
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise ValueError('Existing save expected.')

    def _connect(self):
        con = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA query_only=1')
        schema.check_supported(con)
        return con

    @staticmethod
    def _campaign(con):
        state, config = con.execute('SELECT state,config FROM campaign WHERE id=1').fetchone()
        return json.loads(state), json.loads(config)

    @staticmethod
    def _table(con, name):
        return con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone() is not None

    def _session(self, con):
        if not self._table(con, 'session'):
            return {'phase': 'no_scene', 'scene': '', 'pending_action': None}
        return json.loads(con.execute('SELECT payload FROM session WHERE id=1').fetchone()[0])

    def state(self):
        with closing(self._connect()) as con:
            state, config = self._campaign(con)
            session = self._session(con)
            count = con.execute("SELECT COUNT(*) FROM exchanges WHERE role IN ('player','narrator')").fetchone()[0] \
                if self._table(con, 'exchanges') else 0
            last = con.execute('SELECT MAX(seq) FROM exchanges').fetchone()[0] if self._table(con, 'exchanges') else None
            return {'campaign': state['campaign_id'], 'state_version': state['state_version'],
                    'clock': {'world_minute': state['world_minute'], 'date': date(state['world_minute'])},
                    'scene': session.get('scene', ''), 'phase': session.get('phase'),
                    'has_pending_action': bool(session.get('pending_action')),
                    'exchange_count': count, 'last_seq': last,
                    'creations_tab': config.get('creation_format') == '1',
                    # Page presentation: character name and subtitle are free per campaign.
                    'character': config.get('player_name'),
                    'subtitle': config.get('reader', {}).get('subtitle', '')}

    def config(self):
        """Presentation of the campaign, from its novel pack; the page holds no text of its own universe."""
        from .pack import clean_theme
        with closing(self._connect()) as con:
            state, config = self._campaign(con)
        from .ui_text import labels_for, language_code
        theme = clean_theme(config.get('reader'))
        language = config.get('narration_language') or 'en'
        return {'character': config.get('player_name') or state['campaign_id'].replace('_', ' ').title(),
                'language': language, **{**theme, 'labels': labels_for(language, theme['labels'])},
                'ui_language': language_code(language)}

    def story(self, before=None, n=PAGE):
        n = max(1, min(int(n), 100))
        with closing(self._connect()) as con:
            if not self._table(con, 'exchanges'):
                return {'exchanges': [], 'older': False}
            params = [*ROLES]
            where = 'role IN (?,?)'
            if before is not None:
                where += ' AND seq<?'
                params.append(int(before))
            rows = con.execute(f'SELECT seq,role,content FROM exchanges WHERE {where} ORDER BY seq DESC LIMIT ?',
                               (*params, n + 1)).fetchall()
            older = len(rows) > n
            return {'exchanges': [dict(row) for row in reversed(rows[:n])], 'older': older}

    def search(self, query):
        query = (query or '').strip()
        if not query:
            return {'exchanges': []}
        pattern = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
        with closing(self._connect()) as con:
            if not self._table(con, 'exchanges'):
                return {'exchanges': []}
            rows = con.execute("SELECT seq,role,content FROM exchanges WHERE role IN (?,?) AND content LIKE ? ESCAPE '\\' "
                               "ORDER BY seq DESC LIMIT ?", (*ROLES, pattern, SEARCH_LIMIT)).fetchall()
            return {'exchanges': [dict(row) for row in reversed(rows)]}

    def possessions(self):
        with closing(self._connect()) as con:
            state, config = self._campaign(con)
            return _clean(possessions_packet(state, config, 'player'))

    def npc(self):
        """Public sheets, without reading the NPCs' intentions or knowledge."""
        with closing(self._connect()) as con:
            identities = set()
            sources = []
            if self._table(con, 'events'):
                for row in con.execute('SELECT payload FROM events ORDER BY rowid'):
                    event = json.loads(row[0])
                    if 'player' not in event['audience']:
                        continue
                    identities.update(a[4:] for a in event['audience'] if a.startswith('npc:'))
                    for change in event['changes']:
                        path = change['path'].split('/')
                        if len(path) > 2 and path[1] in ('positions', 'intentions', 'knowledge', 'beliefs'):
                            identities.add(path[2])
                    sources.append((event['description'], None))
            if self._table(con, 'exchanges'):
                # The story provides the exact descriptions, with no generation or extraction from the canon.
                sources = [(row['content'], row['seq']) for row in con.execute(
                    "SELECT content,seq FROM exchanges WHERE role='narrator' ORDER BY seq")] + sources
            sheets = {}
            for identifier in sorted(identities - {'player', 'system', 'faction'}):
                parts = identifier.replace('_', ' ').split()
                accents = {'a': '[aàâä]', 'e': '[eéèêë]', 'i': '[iîï]', 'o': '[oôö]',
                           'u': '[uùûü]', 'c': '[cç]'}
                parts = [''.join(accents.get(c, re.escape(c)) for c in part) for part in parts]
                pattern = re.compile(r'\b' + r'\s+(?:(?:de|du|des)\s+)?'.join(parts) + r'\b', re.I)
                excerpts = []
                name = None
                for text, seq in sources:
                    for paragraph in text.split('\n'):
                        match = pattern.search(paragraph)
                        if match and paragraph.strip():
                            name = name or match.group(0)
                            excerpt = paragraph.strip()
                            if len(excerpt) > 700:
                                start = max(0, match.start() - 180)
                                excerpt = ('…' if start else '') + excerpt[start:start + 700] + '…'
                            if excerpt not in [e['text'] for e in excerpts]:
                                excerpts.append({'text': excerpt, 'seq': seq})
                        if len(excerpts) == 3:
                            break
                    if len(excerpts) == 3:
                        break
                if excerpts:
                    sheets[identifier] = {'id': identifier, 'name': name[:1].upper() + name[1:],
                                          'description': [], 'excerpts': excerpts, 'status': ''}
            # Read all the latest sheets, without the narrator's context budget.
            if CreationRegistry(con).exists():
                for row in con.execute('''SELECT payload FROM creation_revisions r WHERE seq=(
                    SELECT MAX(seq) FROM creation_revisions WHERE entity_id=r.entity_id)'''):
                    value = json.loads(row[0])
                    if value['kind'] != 'npc' or 'player' not in value['audience']:
                        continue
                    identifier = value['entity_id']
                    existing = next((key for key, sheet in sheets.items()
                                     if sheet['name'].casefold() == value['name'].casefold()), identifier)
                    previous = sheets.pop(existing, {})
                    sheets[identifier] = {'id': identifier, 'name': value['name'],
                        'description': value['traits'], 'status': value['status'],
                        'excerpts': previous.get('excerpts', [])}
            return _clean({'npc': sorted(sheets.values(), key=lambda sheet: sheet['name'].casefold())})

    def agenda(self):
        with closing(self._connect()) as con:
            state, _ = self._campaign(con)
            agenda = Agenda(con)
            entries = agenda.entries('player', required_through=state['world_minute']) if agenda.exists() else []
            visible = ('entry_id', 'title', 'actor', 'location', 'kind', 'due_minute', 'status', 'date')
            return {'clock': {'date': date(state['world_minute'])},
                    'events': [{key: item[key] for key in visible if key in item} for item in entries]}

    def summaries(self):
        with closing(self._connect()) as con:
            return _clean({'summaries': WorldPlanner(con).summaries('player', 10)})

    def abilities(self, query=''):
        with closing(self._connect()) as con:
            state, config = self._campaign(con)
            return _clean(skills_packet(state, config, 'player', query))

    def creations(self):
        with closing(self._connect()) as con:
            _, config = self._campaign(con)
            if config.get('creation_format') != '1' or not CreationRegistry(con).exists():
                return None
            return _clean({'creations': CreationRegistry(con).packet('', 'player')})
