"""Local reading interface: read-only, player audience, controlled hosts and methods."""
from contextlib import closing
import hashlib
import http.client
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from novel_engine.agenda import Agenda
from novel_engine.campaign import Campaign
from novel_engine.contracts import check_json
from novel_engine.demo import initial_state, configuration, event, proposal
from novel_engine.planner import WorldPlanner
from novel_engine.scripts.read_game import Turns, make_server

SECRET = 'SECRET_'


def item(quantity, audience=('narrator', 'player'), references=('demo:initial_configuration',), equipped=False):
    return {'owner_id': 'player', 'quantity': quantity, 'equipped': equipped, 'description': 'An ordinary object.',
            'references': list(references), 'audience': list(audience), 'location': 'on:player',
            'condition': 'Sain', 'quantity_precision': 'exact' if quantity is not None else 'unknown',
            'possession_status': 'owns', 'unit': 'coin'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class LectureTests(unittest.TestCase):
    @classmethod
    def build(cls, folder, creations=False):
        state, config = initial_state(), configuration()
        state['resources']['player_silver'] = 22
        state['resources']['player_hp'] = 100
        state['resources']['player_hp_max'] = 100
        state['inventory']['wood'] = item(5, references=('demo:initial_configuration', 'canon:volume1:p10'))
        state['inventory']['unknown_lot'] = item(None)
        state['inventory'][SECRET + 'object'] = item(1, audience=('narrator',))
        state['powers']['power_public'] = {'actor_id': 'player', 'description': 'Pouvoir public : effect visible.',
            'limits': 'Limits known.', 'references': ['demo:initial_configuration'], 'audience': ['narrator', 'player']}
        state['powers'][SECRET + 'power'] = {'actor_id': 'player', 'description': SECRET + 'POWER: hidden.',
            'limits': 'x', 'references': ['demo:initial_configuration'], 'audience': ['narrator']}
        config['possession_rules'] = {'version': 1, 'currencies': {
            'player_silver': {'owner_id': 'player', 'label': 'Silver coins'}}}
        config['resource_owners']['player_silver'] = 'player'
        if creations:
            config['creation_format'] = '1'
        path = Path(folder) / 'campaign.sqlite'
        campaign = Campaign(path).create(state, config)
        campaign.open_scene('The building site waits in the rain.')
        secret = event(campaign.state(), SECRET + 'hidden EVENT', [], audience=['narrator'])
        campaign.apply(proposal(campaign.state(), 'A public turn.', [secret]))
        with campaign.transaction() as con:
            for n in range(1, 46):
                role = 'player' if n % 2 else 'narrator'
                text = f'Action number {n}.' if role == 'player' else f'Narration number {n}.\n\nSecond paragraph {n}.'
                con.execute('INSERT INTO exchanges(message_id,role,content) VALUES (?,?,?)', (f'x{n}', role, text))
            con.execute('INSERT INTO exchanges(message_id,role,content) VALUES (?,?,?)', ('internal', 'system', SECRET + 'ROLE'))
            agenda = Agenda(con)
            agenda.initialize()
            for identifier, audience, title in (('public:1', ['narrator', 'player'], 'Public appointment'),
                                                ('secret:1', ['narrator'], SECRET + 'AGENDA')):
                agenda._put({'entry_id': identifier, 'expected_revision': -1, 'title': title, 'actor': 'x', 'location': 'here',
                             'kind': 'appointment', 'due_minute': 5000, 'conditions': [], 'references': ['canon:volume2:p3'],
                             'audience': audience, 'interrupt': False, 'status': 'planned', 'reason': SECRET + 'REASON',
                             'outcome_event_index': None}, 'test', 0)
            planner = WorldPlanner(con)
            planner.initialize()
            planner._summary(1, 5, {'text': 'Public summary.', 'references': ['exchange:1', 'canon:volume1:p1']}, ['narrator', 'player'])
            planner._summary(6, 9, {'text': SECRET + 'private SUMMARY'}, ['narrator'])
            con.execute('INSERT INTO planner_situations VALUES (?,?,?,?)',
                        ('sit', 0, 'active', check_json({'title': SECRET + 'SITUATION', 'goal': 'x'})))
            con.execute('CREATE TABLE IF NOT EXISTS narrative_attempts (request_id TEXT, status TEXT, proposal TEXT)')
            con.execute('INSERT INTO narrative_attempts VALUES (?,?,?)', ('r', 'recorded', SECRET + 'ATTEMPT canon:volume9'))
        if creations:
            from novel_engine.creations import CreationRegistry
            with campaign.transaction() as con:
                CreationRegistry(con).initialize()
        return campaign

    def setUp(self):
        base = ROOT / '.tmp-tests'
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.campaign = self.build(self.temp.name)
        self.start()

    def start(self, path=None):
        self.server = make_server(path or self.campaign.path, 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def tearDown(self):
        self.stop()
        self.temp.cleanup()

    def request(self, path, method='GET', host=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        headers = {'Host': host} if host else {}
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, body

    def get(self, path):
        status, body = self.request(path)
        self.assertEqual(status, 200, path)
        return json.loads(body.decode('utf-8'))

    ROUTES = ('/api/state', '/api/story?n=100', '/api/search?q=SECRET', '/api/search?q=num', '/api/possessions',
              '/api/agenda', '/api/summaries', '/api/abilities', '/api/npc', '/')

    def test_npc_public_history_registry_and_private_data(self):
        from novel_engine.creations import CreationRegistry
        state = self.campaign.state()
        with self.campaign.transaction() as con:
            CreationRegistry(con).initialize()
            for seq, (identifier, name, kind, audience, traits) in enumerate([
                ('old', 'Ancien', 'npc', ['narrator', 'player'], ['Ancienne description.']),
                ('old', 'Ancien', 'npc', ['narrator', 'player'], ['Updated description.']),
                ('secret', SECRET + 'NPC', 'npc', ['narrator'], [SECRET + 'TRAIT']),
                ('place', 'The square', 'place', ['narrator', 'player'], ['Lieu public.']),
                *[(f'npc_{n}', f'Character {n}', 'npc', ['narrator', 'player'], ['a' * 790] * 12)
                  for n in range(3)]
            ]):
                con.execute('INSERT INTO creation_revisions(entity_id,request_id,payload) VALUES (?,?,?)',
                    (identifier, str(seq), check_json({'entity_id': identifier, 'name': name,
                     'kind': kind, 'audience': audience, 'traits': traits, 'status': 'established'})))
            public = event(state, 'Tomas is a traveler. Lena is a seamstress.', [], audience=['narrator', 'player', 'npc:tomas', 'npc:lena'])
            private = event(state, SECRET + 'HISTOIRE', [], audience=['narrator', 'npc:secret'])
            for value in (public, private):
                con.execute('INSERT INTO events(event_id,state_version,payload) VALUES (?,?,?)',
                            (value['event_id'], state['state_version'], check_json(value)))
            con.execute('INSERT INTO exchanges(message_id,role,content) VALUES (?,?,?)',
                        ('npc_description', 'narrator', 'Tomas wears a brown cloak.'))
        before = sha(self.campaign.path)
        data = self.get('/api/npc')['npc']
        self.assertEqual(sha(self.campaign.path), before)
        self.assertNotIn(SECRET, json.dumps(data))
        self.assertEqual(len(data), 6, 'no context budget truncates the sheets')
        by_id = {p['id']: p for p in data}
        self.assertEqual(by_id['lena']['name'], 'Lena')
        self.assertEqual(by_id['old']['description'], ['Updated description.'])
        self.assertEqual(by_id['tomas']['excerpts'][0]['text'], 'Tomas wears a brown cloak.')
        self.assertIsNotNone(by_id['tomas']['excerpts'][0]['seq'])
        self.assertNotIn('place', by_id)

    def test_npc_empty_without_registry_or_identified_character(self):
        self.assertEqual(self.get('/api/npc'), {'npc': []})

    def test_database_is_never_modified_even_with_a_writer_holding_the_lock(self):
        path = self.campaign.path
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as watcher:
            watcher.execute('SELECT 1 FROM campaign').fetchone()
            version = watcher.execute('PRAGMA data_version').fetchone()[0]
            before = sha(path)
            wal = Path(str(path) + '-wal')
            wal_before = wal.stat().st_size if wal.exists() else 0
            writer = subprocess.Popen([sys.executable, '-c',
                'import sqlite3,sys;c=sqlite3.connect(sys.argv[1],timeout=1);c.execute("BEGIN IMMEDIATE");'
                'print("ready",flush=True);sys.stdin.read()', str(path)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(writer.stdout.readline().strip(), 'ready')
                for route in self.ROUTES:
                    status, _ = self.request(route)
                    self.assertEqual(status, 200, route)
            finally:
                writer.stdin.close()
                writer.wait(timeout=10)
                writer.stdout.close()
            self.assertEqual(watcher.execute('PRAGMA data_version').fetchone()[0], version)
        self.assertEqual(sha(path), before)
        self.assertEqual(wal.stat().st_size if wal.exists() else 0, wal_before)

    def test_pagination_search_and_anchor_ids(self):
        state = self.get('/api/state')
        self.assertEqual(state['campaign'], 'demo')
        self.assertFalse(state['has_pending_action'])
        self.assertEqual(state['creations_tab'], False)
        page = self.get('/api/story?n=20')
        self.assertEqual(len(page['exchanges']), 20)
        self.assertTrue(page['older'])
        seqs = [e['seq'] for e in page['exchanges']]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(page['exchanges'][-1]['content'].split('\n')[0] in ('Narration number 44.', 'Action number 45.'), True)
        older = self.get(f'/api/story?n=20&before={seqs[0]}')
        self.assertEqual([e['seq'] for e in older['exchanges']][-1], seqs[0] - 1)
        everything = self.get('/api/story?n=100')
        self.assertFalse(everything['older'])
        self.assertEqual({e['role'] for e in everything['exchanges']}, {'player', 'narrator'})
        found = self.get('/api/search?q=' + 'number+7')['exchanges']
        self.assertTrue(found and all('number 7' in e['content'] for e in found))
        self.assertEqual(self.get('/api/search?q=%25')['exchanges'], [], 'the LIKE wildcard is escaped')
        self.assertIn('exchange-', (ROOT / 'novel_engine/reader_page.html').read_text(encoding='utf-8'))

    def test_player_audience_only_and_no_canon_reference(self):
        bodies = []
        for route in self.ROUTES:
            status, body = self.request(route)
            self.assertEqual(status, 200, route)
            if route != '/':
                bodies.append(body.decode('utf-8'))
        text = '\n'.join(bodies)
        self.assertNotIn(SECRET, text)
        self.assertNotIn('canon:', text)
        for name in ('narrative_attempts', 'planner_', 'canon_selection_contexts', 'memory_recovery', 'agenda_receipts',
                     'packet_turn_current'):
            self.assertNotIn(name, text)
        self.assertIn('Public appointment', text)
        self.assertIn('Public summary.', text)
        self.assertIn('Pouvoir public', text)

    def test_unknown_quantity_stays_null_and_money_is_shown(self):
        data = self.get('/api/possessions')
        self.assertIsNone(data['possessions']['unknown_lot']['quantity'])
        self.assertEqual(data['possessions']['wood']['quantity'], 5)
        self.assertEqual(data['purse']['player_silver']['balance'], 22)
        self.assertNotIn(SECRET + 'object', data['possessions'])
        self.assertEqual([e['title'] for e in self.get('/api/agenda')['events']], ['Public appointment'])

    def test_creations_tab_only_when_the_format_is_active(self):
        self.assertEqual(self.request('/api/creations')[0], 404)
        self.assertFalse(self.get('/api/state')['creations_tab'])
        self.stop()
        other = tempfile.TemporaryDirectory(dir=ROOT / '.tmp-tests')
        try:
            campaign = self.build(other.name, creations=True)
            self.start(campaign.path)
            self.assertTrue(self.get('/api/state')['creations_tab'])
            self.assertEqual(self.get('/api/creations')['creations'], [])
        finally:
            self.stop()
            other.cleanup()
            self.start()

    def test_only_get_and_local_hosts_are_accepted(self):
        for method in ('POST', 'PUT', 'DELETE', 'PATCH', 'HEAD', 'OPTIONS'):
            self.assertEqual(self.request('/api/state', method)[0], 405, method)
        self.assertEqual(self.request('/api/turn', 'PUT')[0], 405)
        self.assertEqual(self.request('/api/state', host='evil.example')[0], 403)
        self.assertEqual(self.request('/api/state', host='127.0.0.1:1')[0], 403)
        self.assertEqual(self.request('/api/state', host=f'localhost:{self.port}')[0], 200)
        self.assertEqual(self.request('/api/nonexistent')[0], 404)
        self.assertEqual(self.server.server_address[0], '127.0.0.1')

    def post(self, corps, host=None, origin=None, ctype='application/json'):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        headers = {'Content-Type': ctype}
        if host:
            headers['Host'] = host
        if origin:
            headers['Origin'] = origin
        connection.request('POST', '/api/turn', body=json.dumps(corps).encode('utf-8'), headers=headers)
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, json.loads(body.decode('utf-8'))

    def wait_turn(self):
        for _ in range(100):
            state = self.get('/api/turn')
            if not state['in_progress']:
                return state
            time.sleep(0.1)
        self.fail('turn never finished')

    def test_turn_goes_through_official_script_one_at_a_time(self):
        lent = [sys.executable, '-c', "import time;time.sleep(0.6);print('{\"status\":\"recorded\",\"narration\":\"x\"}')"]
        with mock.patch.object(Turns, 'command', return_value=lent) as command:
            self.assertEqual(self.post({'text': 'I look.', 'engine': 'claude'})[0], 202)
            self.assertEqual(self.post({'text': 'Encore.', 'engine': 'codex'})[0], 409)
            state = self.wait_turn()
        self.assertEqual(state['result'], {'status': 'recorded'})
        self.assertIsNone(state['error'])
        command.assert_called_once_with('I look.', 'claude', 'new')

    def test_dictation_only_fills_the_field_and_never_sends(self):
        page = (ROOT / 'novel_engine/reader_page.html').read_text(encoding='utf-8')
        self.assertIn('id="micro"', page)
        corps = page.split('reco.onresult=', 1)[1].split('reco.onerror=', 1)[0]
        self.assertNotIn('send(', corps)
        self.assertNotIn('submit', corps)
        self.assertIn("reco.lang=cfg.language||'en'", page)

    def test_turn_command_uses_the_official_script_and_modes(self):
        turns = Turns('c.sqlite')
        base = turns.command('I leave.', 'claude', 'new')
        self.assertTrue(base[1].endswith('play_narrative.py'))
        self.assertEqual(base[-1], '--text=I leave.')
        self.assertEqual(turns.command('--resume', 'codex', 'new')[-1], '--text=--resume')
        self.assertIn('--json', base)
        self.assertEqual(turns.command('', 'codex', 'retry')[-2:], ['--resume', '--retry'])
        self.assertEqual(turns.command('', 'codex', 'reevaluate')[-1], '--reevaluate')

    def test_turn_failure_is_reported(self):
        casse = [sys.executable, '-c', 'import sys;sys.stderr.write("boum");sys.exit(1)']
        with mock.patch.object(Turns, 'command', return_value=casse):
            self.assertEqual(self.post({'text': 'I jump.'})[0], 202)
            self.assertIn('boum', self.wait_turn()['error'])

    def test_turn_post_is_guarded(self):
        with mock.patch.object(Turns, 'command') as command:
            self.assertEqual(self.post({'text': 'x'}, host='evil.example')[0], 403)
            self.assertEqual(self.post({'text': 'x'}, origin='http://evil.example')[0], 403)
            self.assertEqual(self.post({'text': 'x'}, ctype='text/plain')[0], 415)
            self.assertEqual(self.post({'text': '  '})[0], 400)
            self.assertEqual(self.post({'text': 'x', 'engine': 'other'})[0], 400)
            self.assertEqual(self.post({'text': 'x', 'mode': 'other'})[0], 400)
            command.assert_not_called()

    def test_local_resources_are_served_and_confined(self):
        status, corps = self.request('/resources/icons/broadsword.svg')
        self.assertEqual(status, 200)
        self.assertIn(b'<svg', corps)
        status, corps = self.request('/resources/fonts/cinzel.ttf')
        self.assertEqual(status, 200)
        self.assertGreater(len(corps), 10000)
        for path in ('/resources/icons/../../reader_page.html', '/resources/icons/%2e%2e/reader_page.html',
                       '/resources/fonts/OFL-Cinzel.txt', '/resources/autre/x.svg', '/resources/icons/absent.svg',
                       '/resources/icons/sous/x.svg', '/resources/'):
            self.assertEqual(self.request(path)[0], 404, path)
        self.assertEqual(self.request('/resources/icons/broadsword.svg', host='evil.example')[0], 403)

    def test_page_has_no_external_resource_and_escapes_content(self):
        page = (ROOT / 'novel_engine/reader_page.html').read_text(encoding='utf-8')
        for forbidden in ('http://', 'https://', 'innerHTML', 'src="//', "href=\"//"):
            self.assertNotIn(forbidden, page)
        self.assertIn('prefers-color-scheme', page)
        self.assertIn('localStorage', page)
        self.assertIn('Georgia', page)
        status, body = self.request('/')
        self.assertEqual(status, 200)
        self.assertEqual(body.decode('utf-8'), page)


if __name__ == '__main__':
    unittest.main()
