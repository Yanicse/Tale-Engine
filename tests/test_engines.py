"""Anthropic API and manual narrators, and the engine factory."""
import json
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from novel_engine import engines
from novel_engine.adapters import ModelUnavailable
from novel_engine.contracts import ROOT
from novel_engine.narration_api import ApiNarrator, ApiPlanner
from novel_engine.narration_claude import ClaudeNarrator
from novel_engine.narration_codex import CodexNarrator
from novel_engine.narration_manual import ManualNarrator, ManualPlanner

PROMPT = 'Rules of the game.\nCURRENT TURN\n{"allowed_references": []}'
SCHEMA = {'$schema': 'http://json-schema.org/draft-07/schema#', 'type': 'object',
          'properties': {'narration': {'type': 'string'}}, 'required': ['narration']}


class FakeApi(BaseHTTPRequestHandler):
    reply = (200, {'content': [{'type': 'tool_use', 'name': 'answer', 'input': {'narration': 'Hello.'}}],
                   'stop_reason': 'tool_use', 'usage': {'input_tokens': 12, 'output_tokens': 3}})
    seen = []

    def do_POST(self):
        FakeApi.seen.append({'headers': {k.lower(): v for k, v in self.headers.items()}, 'body': json.loads(self.rfile.read(int(self.headers['Content-Length'])))})
        status, payload = FakeApi.reply
        data = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        pass


class ApiNarratorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(('127.0.0.1', 0), FakeApi)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = 'http://127.0.0.1:%d/v1/messages' % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakeApi.seen = []
        FakeApi.reply = (200, {'content': [{'type': 'tool_use', 'name': 'answer', 'input': {'narration': 'Hello.'}}],
                               'stop_reason': 'tool_use', 'usage': {'input_tokens': 12, 'output_tokens': 3}})
        self.narrator = ApiNarrator(api_key='sk-test')
        self.narrator.url = self.url

    def call(self):
        return self.narrator.call(PROMPT, SCHEMA, None, None)

    def test_forced_tool_call_returns_the_structured_answer(self):
        self.assertEqual(json.loads(self.call()), {'narration': 'Hello.'})
        sent = FakeApi.seen[0]
        self.assertEqual(sent['headers']['x-api-key'], 'sk-test')
        body = sent['body']
        self.assertEqual(body['tool_choice'], {'type': 'tool', 'name': 'answer'})
        self.assertNotIn('$schema', body['tools'][0]['input_schema'])
        self.assertTrue(body['system'].startswith('Rules of the game'))
        self.assertIn('{"allowed_references"', body['messages'][0]['content'])
        self.assertEqual(self.narrator.last_metrics['usage'], {'input_tokens': 12, 'output_tokens': 3})

    def test_missing_key_makes_no_call(self):
        self.narrator.api_key = None
        with self.assertRaises(ModelUnavailable):
            self.call()
        self.assertEqual(FakeApi.seen, [])

    def test_http_error_cut_answer_and_missing_tool_are_reported(self):
        for reply, expected in (((401, {'error': {'message': 'bad key'}}), '401'),
                                ((200, {'content': [], 'stop_reason': 'max_tokens'}), 'output limit'),
                                ((200, {'content': [{'type': 'text', 'text': 'hi'}], 'stop_reason': 'end_turn'}), 'without the structured')):
            FakeApi.reply = reply
            with self.assertRaises(ModelUnavailable) as caught:
                self.call()
            self.assertIn(expected, str(caught.exception))

    def test_key_is_not_kept_in_the_request_body_or_metrics(self):
        self.call()
        self.assertNotIn('sk-test', json.dumps(FakeApi.seen[0]['body']) + json.dumps(self.narrator.last_metrics))

    def test_planner_uses_the_same_transport(self):
        planner = ApiPlanner(api_key='sk-test')
        planner.url = self.url
        self.assertEqual(json.loads(planner.call(PROMPT, SCHEMA, None, None)), {'narration': 'Hello.'})


class ManualNarratorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='.tmp-manual-', dir=ROOT))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.narrator = ManualNarrator(self.tmp / 'manual')

    def test_first_call_writes_the_request_and_asks_for_an_answer(self):
        with self.assertRaises(ModelUnavailable) as caught:
            self.narrator.call(PROMPT, SCHEMA, None, None)
        request = (self.tmp / 'manual' / 'turn.prompt.md').read_text(encoding='utf-8')
        self.assertIn('Rules of the game', request)
        self.assertIn('"narration"', request)
        self.assertNotIn('$schema', request)
        self.assertIn('turn.response.json', str(caught.exception))

    def test_answer_is_used_only_for_the_request_it_answers(self):
        with self.assertRaises(ModelUnavailable):
            self.narrator.call(PROMPT, SCHEMA, None, None)
        answer = self.tmp / 'manual' / 'turn.response.json'
        answer.write_text('{"narration": "Done."}', encoding='utf-8')
        self.assertEqual(self.narrator.call(PROMPT, SCHEMA, None, None), '{"narration": "Done."}')
        self.assertEqual(self.narrator.last_metrics['model_calls'], 0)
        # A different request replaces the files and discards the stale answer.
        with self.assertRaises(ModelUnavailable):
            self.narrator.call(PROMPT + ' changed', SCHEMA, None, None)
        self.assertFalse(answer.exists())

    def test_measured_durations_do_not_change_the_request(self):
        first = 'Rules of the game.\nCURRENT TURN\n' + json.dumps({
            'preparation_seconds': 0.0013, 'model_seconds': 1.5, 'search_memory': {'seconds': 0.001}})
        with self.assertRaises(ModelUnavailable):
            self.narrator.call(first, SCHEMA, None, None)
        (self.tmp / 'manual' / 'turn.response.json').write_text('{"narration": "Done."}', encoding='utf-8')
        again = 'Rules of the game.\nCURRENT TURN\n' + json.dumps({
            'preparation_seconds': 0.0020, 'model_seconds': 9.25, 'search_memory': {'seconds': 0.002}})
        self.assertEqual(self.narrator.call(again, SCHEMA, None, None), '{"narration": "Done."}')

    def test_gameplay_durations_invalidate_the_previous_answer(self):
        for field in ('seconds', 'duration_seconds', 'travel_seconds', 'model_seconds'):
            with self.subTest(field=field):
                prefix = 'Rules of the game.\nCURRENT TURN\n'
                first = prefix + json.dumps({'campaign_rules': {'character_rules': {
                    'abilities': {'ward': {'parameters': {field: 60}}}}}})
                with self.assertRaises(ModelUnavailable):
                    self.narrator.call(first, SCHEMA, None, None)
                answer = self.tmp / 'manual' / 'turn.response.json'
                answer.write_text('{"narration":"Done."}', encoding='utf-8')
                changed = first.replace(': 60', ': 120')
                with self.assertRaises(ModelUnavailable):
                    self.narrator.call(changed, SCHEMA, None, None)
                self.assertFalse(answer.exists())

    def test_answer_schema_changes_invalidate_the_previous_answer(self):
        with self.assertRaises(ModelUnavailable):
            self.narrator.call(PROMPT, SCHEMA, None, None)
        answer = self.tmp / 'manual' / 'turn.response.json'
        answer.write_text('{"narration":"Done."}', encoding='utf-8')
        schema = {**SCHEMA, 'required': ['narration', 'events'], 'properties': {
            **SCHEMA['properties'], 'events': {'type': 'array', 'items': {'type': 'string'}}}}
        with self.assertRaises(ModelUnavailable):
            self.narrator.call(PROMPT, schema, None, None)
        self.assertFalse(answer.exists())

    def test_planner_has_its_own_files(self):
        with self.assertRaises(ModelUnavailable):
            ManualPlanner(self.tmp / 'manual').call(PROMPT, SCHEMA, None, None)
        self.assertTrue((self.tmp / 'manual' / 'review.prompt.md').is_file())
        self.assertFalse((self.tmp / 'manual' / 'turn.prompt.md').exists())


class FactoryTest(unittest.TestCase):
    def test_each_engine_builds_a_narrator_and_a_planner(self):
        expected = {'codex': CodexNarrator, 'claude': ClaudeNarrator, 'api': ApiNarrator, 'manual': ManualNarrator}
        for name in engines.ENGINES:
            narrator, planner, model = engines.create(name, ROOT / '.tmp-none')
            self.assertIsInstance(narrator, expected[name])
            self.assertTrue(hasattr(planner, 'generate'))
        self.assertEqual(engines.create('manual', ROOT)[2], 'manual')

    def test_manual_is_always_available(self):
        with mock.patch('novel_engine.narration_claude.find_claude', return_value=None), \
             mock.patch('novel_engine.narration_codex.find_codex', return_value=None), \
             mock.patch.dict('os.environ', {}, clear=True):
            self.assertEqual(engines.available(), ['manual'])
            self.assertEqual(engines.default_engine('claude'), 'manual')


if __name__ == '__main__':
    unittest.main()
