"""Indexed memory regressions on copies of a fictional played history, with no remote call."""
from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from novel_engine.adapters import ModelUnavailable
from novel_engine.campaign import Campaign
from novel_engine.demo import initial_state, configuration
from novel_engine.indexed_memory import MemoryIndex
from novel_engine.narration_codex import normalize_response
from novel_engine.narrative import NarrativeLoop
from novel_engine.scripts.migrate_memory import game_fingerprint, migrate
from novel_engine.scripts.play_narrative import main

REPORT = ("The examiner at the guild reads Mira's file aloud. The evaluation is favorable. A signed report, "
          "addressed to Captain Oren and Captain Lysa, is closed with a copper seal. The badge was made by hand "
          "from adamantite, but the examiner gives no confirmation of any promotion nor of a decision.")
CONSTRUCTION = 'Mira inspects the house site. The planks are stacked beside the frame and the land is still soft.'
LONG = ' '.join('Stall number %d sells ropes, lanterns and salted fish to the passing sailors.' % n
                for n in range(1, 41))


class RecordedNarrator:
    def __init__(self, response=None):
        self.response = response
        self.queue = []
        self.calls = 0
        self.last_metrics = {}

    def generate(self, prompt):
        self.calls += 1
        return deepcopy(self.queue.pop(0) if self.queue else self.response)


def plain(narration):
    return {'narration': narration, 'events': [], 'blocking_questions': []}


def question(text):
    return {'narration': '', 'events': [], 'blocking_questions': [text]}


class MemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = ROOT / '.tmp-tests'
        base.mkdir(exist_ok=True)
        cls.shared = tempfile.TemporaryDirectory(dir=base)
        cls.source = Path(cls.shared.name) / 'source.sqlite'
        campaign = Campaign(cls.source).create(initial_state(), configuration())
        campaign.open_scene('The harbor wakes under a grey sky.')
        narrator = RecordedNarrator()
        loop = NarrativeLoop(campaign, narrator)
        script = [('I go to the guild hall for my evaluation.', plain(REPORT)),
                  ('I walk to the market.', plain('The market is crowded with sailors and fishmongers.')),
                  ('I look at the long row of stalls.', plain(LONG)),
                  ('I buy some bread.', plain('The baker wraps a warm loaf in cloth.')),
                  ('I follow the quay.', plain('Gulls circle above the moored boats.')),
                  ('I go to the harbor house.', plain(CONSTRUCTION)),
                  ('Take it.', question('Which object do you mean?')),
                  ('I sit by the water.', plain('The tide climbs slowly against the stones.')),
                  ('I count the boats.', plain('Eleven boats are tied along the quay.')),
                  ('Take that one.', question('Which boat do you mean?')),
                  ('I rest.', plain('The afternoon passes quietly.'))]
        for text, response in script:
            narrator.queue.append(response)
            loop.run_turn(text)
        with campaign.transaction() as con:
            rows = {row['content']: row['seq'] for row in con.execute("SELECT seq,content FROM exchanges WHERE role='narrator'")}
            cls.report_seq, cls.long_seq = rows[REPORT], rows[LONG]
            cls.question_seqs = [rows['Which object do you mean?'], rows['Which boat do you mean?']]
            cls.recorded = {row['seq']: row['content'] for row in con.execute('SELECT seq,content FROM exchanges')}

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def setUp(self):
        base = ROOT / '.tmp-tests'
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.path = Path(self.temp.name) / 'campaign.sqlite'
        with contextlib.closing(sqlite3.connect(self.source.resolve().as_uri() + '?mode=ro', uri=True)) as src, \
             contextlib.closing(sqlite3.connect(self.path)) as dst:
            src.backup(dst)
        self.c = Campaign(self.path)
        self.report_ref = 'exchange:%d' % self.report_seq
        with self.c.transaction() as con:
            self.before = game_fingerprint(con)

    def tearDown(self):
        self.temp.cleanup()

    def _packet(self, action='', previous='', position='', recipient='narrator'):
        with self.c.transaction() as con:
            index = MemoryIndex(con)
            index.sync()
            return index.packet(action, previous, position, recipient)

    @staticmethod
    def _text(packet):
        return ' '.join(item['text'] for item in packet['proofs']) + ' ' + ' '.join(item['content'] for item in packet['exchanges'])

    def test_migration_and_second_sync_preserve_every_original_table(self):
        report = migrate(self.c)
        self.assertTrue(Path(report['backup']).is_file())
        with self.c.transaction() as con:
            count = con.execute('SELECT COUNT(*) FROM memory_sources').fetchone()[0]
            self.assertEqual(MemoryIndex(con).sync()['added_events'], 0)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM memory_sources').fetchone()[0], count)
            self.assertEqual(game_fingerprint(con), self.before)

    def test_rebuild_preserves_sources_and_learned_topic_links(self):
        self._packet('guild')
        with self.c.transaction() as con:
            index = MemoryIndex(con)
            index.topic('old_report', 'Old report', ['old report'], audience=['narrator', 'player'])
            source = con.execute('SELECT doc_id FROM memory_sources WHERE reference=?', (self.report_ref,)).fetchone()[0]
            con.execute("INSERT INTO memory_links VALUES ('old_report',?)", (source,))
            count = con.execute('SELECT COUNT(*) FROM memory_sources').fetchone()[0]
            index.rebuild()
            self.assertEqual(con.execute('SELECT COUNT(*) FROM memory_sources').fetchone()[0], count)
            self.assertIsNotNone(con.execute('''SELECT 1 FROM memory_links l JOIN memory_sources s
                ON s.doc_id=l.doc_id WHERE l.topic_id='old_report' AND s.reference=?''', (self.report_ref,)).fetchone())
            con.execute("INSERT INTO memory_fts(memory_fts,rank) VALUES ('integrity-check',1)")
            self.assertEqual(game_fingerprint(con), self.before)

    def test_guild_old_evaluation_report_address_and_unknown_decision(self):
        packet = self._packet('My earlier evaluation at the Guild')
        text = self._text(packet)
        self.assertIn('favorable', text)
        self.assertIn('copper seal', text)
        self.assertIn('Oren and Captain Lysa', text)
        self.assertIn('made by hand', text)
        self.assertIn('no confirmation', text)
        self.assertIn('guild', [item['id'] for item in packet['files']])

    def test_short_followups_after_a_blocked_input_keep_the_guild_file(self):
        for action, previous in (('Yes, please.', REPORT), ('Go on.', REPORT), ('I wait.', REPORT)):
            with self.subTest(action=action):
                packet = self._packet(action, previous, 'guild_hall_harbor')
                text = self._text(packet)
                self.assertIn('copper seal', text)
                self.assertIn('favorable', text)

    def test_explicit_old_topic_overrides_current_construction_scene(self):
        packet = self._packet('I talk again about my exam with the examiner', CONSTRUCTION, 'harbor_house')
        self.assertIn('guild', packet['search']['subjects'])
        self.assertIn('copper seal', self._text(packet))

    def test_warm_narrative_path_does_not_read_all_events_or_exchanges(self):
        self._packet('guild')
        statements = []
        transaction = self.c.transaction

        @contextlib.contextmanager
        def traced():
            with transaction() as con:
                con.set_trace_callback(statements.append)
                yield con
        with patch.object(self.c, 'transaction', traced), \
             patch.object(self.c, '_snapshot', side_effect=AssertionError('Whole journal')):
            self.c.prepare_turn(narrative_memory=True)
        self.assertFalse(any('SELECT payload FROM events ORDER BY rowid' in sql for sql in statements))
        self.assertFalse(any('FROM exchanges e LEFT JOIN requests' in sql for sql in statements))
        self.assertTrue(any('memory_fts MATCH' in sql for sql in statements))

    def test_technical_questions_are_not_indexed(self):
        self._packet('guild')
        references = tuple('exchange:%d' % seq for seq in self.question_seqs)
        with self.c.transaction() as con:
            marks = ','.join('?' for _ in references)
            self.assertIsNone(con.execute('SELECT 1 FROM memory_sources WHERE reference IN (%s)' % marks, references).fetchone())
            self.assertIsNotNone(con.execute('SELECT 1 FROM memory_sources WHERE reference=?', (self.report_ref,)).fetchone())

    def test_secret_sources_and_their_neighbors_are_filtered(self):
        with self.c.transaction() as con:
            index = MemoryIndex(con)
            index.sync()
            # Remove player access to an already played source, on the copy only.
            con.execute('UPDATE memory_sources SET audience=? WHERE reference=?', (json.dumps(['narrator']), self.report_ref))
            packet = index.packet('signed report copper seal', recipient='player')
            self.assertNotIn(self.report_ref, {item['reference'] for item in packet['proofs']})
            self.assertNotIn(self.report_seq, {item['seq'] for item in packet['exchanges']})

    def test_local_consultation_and_cli_do_not_create_action_or_call_model(self):
        before = self.c.resume()
        out = io.StringIO()
        with patch('novel_engine.narration_codex.CodexNarrator') as model, contextlib.redirect_stdout(out):
            code = main(['--campaign', str(self.path), '--memories', 'guild', '--json'])
        self.assertEqual(code, 0)
        model.assert_not_called()
        self.assertEqual(self.c.resume(), before)
        self.assertIn('copper seal', out.getvalue())

    def test_memory_request_keeps_scene_action_and_requires_explicit_resume(self):
        before = self.c.resume()
        state = self.c.state()
        narrator = RecordedNarrator({'narration': '', 'events': [], 'blocking_questions': [],
            'narrative_schema_version': '1.0', 'memory_updates': [],
            'memory_request': {'query': 'evaluation signed report promotion', 'reason': 'Archives required.'}})
        loop = NarrativeLoop(self.c, narrator)
        first = loop.run_turn('I ask about my evaluation again.')
        self.assertEqual(first['status'], 'required_memory')
        self.assertEqual(self.c.state(), state)
        pending = self.c.resume()['session']['pending_action']
        self.assertEqual(self.c.resume()['session']['scene'], before['session']['scene'])
        self.assertEqual(narrator.calls, 1)
        self.assertEqual(loop.run_turn()['status'], 'resume')
        self.assertEqual(narrator.calls, 1)
        narrator.response = plain(self.recorded[self.report_seq])
        resumed = loop.run_turn(resume=True)
        self.assertEqual(resumed['request_id'], pending['request_id'])
        self.assertEqual(resumed['status'], 'recorded')
        self.assertEqual(narrator.calls, 2)

    def test_repeated_recovery_is_bounded_and_preserves_pending_action(self):
        pending = self.c.begin_action('I ask about my evaluation again.')
        for _ in range(3):
            result = self.c.recover_memory(pending['request_id'], 'guild')
        self.assertEqual(result['search_status'], 'blocked')
        self.assertEqual(self.c.resume()['session']['pending_action']['request_id'], pending['request_id'])

    def test_typed_response_rejects_mixed_memory_and_events(self):
        response = {'narration': '', 'events': [], 'blocking_questions': ['Which object?'],
            'narrative_schema_version': '1.0', 'memory_updates': [],
            'memory_request': {'query': 'guild', 'reason': 'Memory.'}}
        with self.assertRaises(ValueError):
            normalize_response(response)

    def test_new_memo_links_current_saved_source_atomically(self):
        response = {'narration': self.recorded[self.report_seq], 'events': [], 'blocking_questions': [],
            'narrative_schema_version': '1.0', 'memory_request': None,
            'memory_updates': [{'topic_id': 'examiner_report', 'title': 'Examiner report',
                                'aliases': ['old report', 'examiner'], 'references': ['current_turn']}]}
        narrator = RecordedNarrator(response)
        result = NarrativeLoop(self.c, narrator).run_turn('I read the report again.')
        with self.c.transaction() as con:
            links = con.execute('SELECT s.reference FROM memory_links l JOIN memory_sources s ON s.doc_id=l.doc_id WHERE l.topic_id=?',
                                ('examiner_report',)).fetchall()
        self.assertTrue(links)
        self.assertEqual(result['status'], 'recorded')
        self.assertEqual(narrator.calls, 1)

    def test_memo_failure_rolls_back_game_and_reuses_saved_response(self):
        response = {'narration': self.recorded[self.report_seq], 'events': [], 'blocking_questions': [],
            'narrative_schema_version': '1.0', 'memory_request': None,
            'memory_updates': [{'topic_id': 'examiner_report', 'title': 'Report',
                                'aliases': ['examiner'], 'references': ['current_turn']}]}
        narrator = RecordedNarrator(response)
        loop = NarrativeLoop(self.c, narrator)
        before = self.c.state()
        with patch.object(MemoryIndex, 'apply_updates', side_effect=ValueError('Simulated memo failure')):
            with self.assertRaises(ValueError):
                loop.run_turn('I read the report again.')
        self.assertEqual(self.c.state(), before)
        self.assertIsNotNone(self.c.resume()['session']['pending_action'])
        self.assertEqual(loop.run_turn(resume=True)['status'], 'recorded')
        self.assertEqual(narrator.calls, 1)

    def test_secret_topic_title_alias_and_sources_are_not_public(self):
        self._packet('guild')
        with self.c.transaction() as con:
            index = MemoryIndex(con)
            index.topic('secret_intervention', "Examiner's secret", ['old report'])
            doc = con.execute('SELECT doc_id FROM memory_sources WHERE reference=?', (self.report_ref,)).fetchone()[0]
            con.execute("INSERT INTO memory_links VALUES ('secret_intervention',?)", (doc,))
            packet = index.packet('old report', recipient='player')
            self.assertNotIn('secret_intervention', packet['search']['subjects'])
            self.assertNotIn("Examiner's secret", json.dumps(packet, ensure_ascii=False))

    def test_recovery_expands_an_exact_source_without_rewriting_it(self):
        self._packet('guild')
        reference = 'exchange:%d' % self.long_seq
        with self.c.transaction() as con:
            partial = MemoryIndex(con).packet('', query=reference)
        self.assertTrue(next(p for p in partial['proofs'] if p['reference'] == reference)['partial_excerpt'])
        pending = self.c.begin_action('I look at the stalls again.')
        self.c.recover_memory(pending['request_id'], reference)
        prepared = self.c.prepare_turn(narrative_memory=True)
        item = next(p for p in prepared['narrative_memory_context']['proofs'] if p['reference'] == reference)
        self.assertEqual(item['text'], self.recorded[self.long_seq])
        self.assertFalse(item['partial_excerpt'])

    def test_memo_update_unknown_reference_preserves_world_and_action(self):
        narrator = RecordedNarrator({'narration': self.recorded[self.report_seq], 'events': [], 'blocking_questions': [],
            'narrative_schema_version': '1.0', 'memory_request': None,
            'memory_updates': [{'topic_id': 'report', 'title': 'Report', 'aliases': [],
                                'references': ['exchange:999999']}]})
        before = self.c.state()
        with self.assertRaises(ModelUnavailable):
            NarrativeLoop(self.c, narrator).run_turn('I read the report again.')
        self.assertEqual(self.c.state(), before)
        self.assertIsNotNone(self.c.resume()['session']['pending_action'])

    def test_crash_during_local_recovery_keeps_query_for_next_explicit_resume(self):
        reference = 'exchange:%d' % self.long_seq
        narrator = RecordedNarrator({'narration': '', 'events': [], 'blocking_questions': [],
            'narrative_schema_version': '1.0', 'memory_updates': [],
            'memory_request': {'query': reference, 'reason': 'Reread the conclusion.'}})
        loop = NarrativeLoop(self.c, narrator)
        with patch.object(self.c, 'recover_memory', side_effect=OSError('Simulated local stop')):
            with self.assertRaises(OSError):
                loop.run_turn('I ask about my evaluation again.')
        self.assertEqual(narrator.calls, 1)
        pending = self.c.resume()['session']['pending_action']
        narrator.response = plain(self.recorded[self.report_seq])
        result = NarrativeLoop(self.c, narrator).run_turn(resume=True)
        self.assertEqual(result['request_id'], pending['request_id'])
        with self.c.transaction() as con:
            self.assertEqual(con.execute('SELECT query FROM memory_recovery WHERE request_id=?',
                                         (pending['request_id'],)).fetchone()[0], reference)

    def test_terminal_memory_commands_and_hint_do_not_advance_game(self):
        before = self.c.resume()
        out = io.StringIO()
        with patch('novel_engine.narration_codex.CodexNarrator') as model, \
             patch('builtins.input', side_effect=['/memories guild', '/files', '/reminder my exam', '/quit']), \
             patch('sys.stdin.isatty', return_value=False), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['--campaign', str(self.path), '--engine', 'codex']), 0)
        model.return_value.generate.assert_not_called()
        self.assertEqual(self.c.resume(), before)
        self.assertIn('copper seal', out.getvalue())


if __name__ == '__main__':
    unittest.main(verbosity=2)
