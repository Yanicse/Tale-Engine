"""Claude engine: CLI options, prompt splitting, errors and complete journey, with no network."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from novel_engine.adapters import ModelUnavailable
from novel_engine.agenda import Agenda
from novel_engine.campaign import Campaign
from novel_engine.demo import initial_state, configuration
from novel_engine.indexed_memory import MemoryIndex
from novel_engine.narration_claude import ClaudeNarrator, ClaudePlanner, split_prompt
from novel_engine.planner import WorldPlanner
from novel_engine.scripts.play_narrative import main


class LocalClaude(ClaudeNarrator):
    control = None
    def command(self, system_path, schema_text):
        args = super().command(system_path, schema_text)
        return [sys.executable, str(ROOT/'tests/fake_claude.py'), 'narrator', str(self.control), *args[1:]]


class LocalClaudePlanner(ClaudePlanner):
    control = None
    def command(self, system_path, schema_text):
        args = super().command(system_path, schema_text)
        return [sys.executable, str(ROOT/'tests/fake_claude.py'), 'planner', str(self.control), *args[1:]]


class ClaudeUnitTests(unittest.TestCase):
    def test_prompt_split_keeps_exact_text_and_stable_system(self):
        prompt = ('INSTRUCTIONS\n'
                  'PREPARATION DOCUMENTS\n'
                  '[]\n'
                  'CURRENT TURN\n'
                  '{"a":1}')
        system, message = split_prompt(prompt)
        self.assertEqual(system+'\n'+message, prompt)
        self.assertTrue(message.startswith('CURRENT TURN\n'))
        system, message = split_prompt(('instructions\n'
                                        'WORLD REVIEW\n'
                                        '{}'))
        self.assertTrue(message.startswith(('WORLD REVIEW\n')))
        self.assertEqual(split_prompt('Test technique.')[1], 'Test technique.')

    def test_host_session_variables_are_not_inherited(self):
        host = {'CLAUDECODE': '1', 'CLAUDE_CODE_SIMPLE': '1', 'ANTHROPIC_BASE_URL': 'http://127.0.0.1:1',
                'CLAUDE_CODE_OAUTH_SCOPES': 'x', 'PATH': os.environ.get('PATH', '')}
        with patch.dict(os.environ, host, clear=True):
            env = ClaudeNarrator(executable='claude.exe').environment()
        self.assertFalse({'CLAUDECODE', 'CLAUDE_CODE_SIMPLE', 'ANTHROPIC_BASE_URL', 'CLAUDE_CODE_OAUTH_SCOPES'} & set(env))
        self.assertEqual(env['DISABLE_PROMPT_CACHING'], '1')

    def test_command_has_no_tools_and_runs_outside_project(self):
        adapter = ClaudeNarrator(executable='claude.exe')
        with tempfile.TemporaryDirectory() as folder:
            command, payload, cwd = adapter.invocation('C\nCURRENT TURN\n{}', {'$schema': 'x', 'type': 'object'}, Path(folder), Path(folder))
            self.assertEqual(Path(command[command.index('--system-prompt-file')+1]).read_text(encoding='utf-8'), 'C')
        self.assertEqual(command[command.index('--tools')+1], '')
        self.assertNotIn(str(ROOT), cwd)
        self.assertEqual(payload.decode('utf-8'), 'CURRENT TURN\n{}')
        self.assertNotIn('--fast', command)
        self.assertEqual(json.loads(command[command.index('--json-schema')+1]), {'type': 'object'})

    def test_errors_are_classified_with_claude_advice(self):
        for text, kind in (('Not logged in · Please run /login', 'authentication'),
                           ('API Error: 529 overloaded', 'service'), ('Claude usage limit reached', 'usage_limit')):
            process = Mock(); process.returncode = 1; process.poll.return_value = 0
            process.communicate.return_value = (json.dumps({'type': 'result', 'is_error': True, 'result': text}).encode(), b'')
            adapter = ClaudeNarrator(executable='claude.exe')
            with patch('novel_engine.narration_codex.subprocess.Popen', return_value=process):
                with self.assertRaises(ModelUnavailable) as raised: adapter.generate('Test technique.')
            self.assertEqual(adapter.last_error['category'], kind)
            self.assertIn('Claude', str(raised.exception))
            if kind == 'authentication': self.assertIn('claude auth login', str(raised.exception))

    def test_usage_is_reported_like_codex(self):
        usage = ClaudeNarrator.usage({'usage': {'input_tokens': 100, 'cache_read_input_tokens': 900,
                                                'cache_creation_input_tokens': 50, 'output_tokens': 7}})
        self.assertEqual((usage['input_tokens'], usage['cached_input_tokens'], usage['cache_write_input_tokens']), (1050, 900, 50))


class EngineChoiceTests(unittest.TestCase):
    def test_interactive_launch_asks_until_valid_answer(self):
        from novel_engine.scripts.play_narrative import choose_engine
        with patch('novel_engine.engines.available', return_value=['codex', 'claude', 'manual']):
            for answers, expected in ((['claude'], 'claude'), (['  Claude '], 'claude'), ([''], 'codex'),
                                      (['gpt', 'codex'], 'codex'), (['api'], 'api'), (['manual'], 'manual')):
                with patch('builtins.input', side_effect=answers), patch('sys.stderr', io.StringIO()):
                    self.assertEqual(choose_engine(), expected)
            with patch('builtins.input', side_effect=EOFError), patch('sys.stderr', io.StringIO()):
                self.assertEqual(choose_engine(), 'codex')

    def test_default_engine_is_the_saved_one_if_it_still_works_else_the_first_available(self):
        from novel_engine.engines import default_engine
        with patch('novel_engine.engines.available', return_value=['claude', 'manual']):
            self.assertEqual(default_engine(None), 'claude')
            self.assertEqual(default_engine('manual'), 'manual')
            self.assertEqual(default_engine('codex'), 'claude')
        with patch('novel_engine.engines.available', return_value=['manual']):
            self.assertEqual(default_engine('api'), 'manual')


class ClaudeLoopTests(unittest.TestCase):
    def setUp(self):
        base = ROOT/'.tmp-tests'; base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.c = Campaign(Path(self.temp.name)/'test.sqlite').create(initial_state('audit'), configuration())
        self.c.open_scene('You wait for a first step at the door.')
        self.control = Path(self.temp.name)/'check.txt'
        LocalClaude.control = LocalClaudePlanner.control = self.control
        with self.c.transaction() as con:
            Agenda(con).initialize(); MemoryIndex(con).sync()
            p = WorldPlanner(con); p.initialize()
            from test_planner import install_test_chronicle
            install_test_chronicle(con)

    def tearDown(self): self.temp.cleanup()

    def test_turns_and_planner_with_claude_engine(self):
        for i in range(11):
            output = io.StringIO()
            with patch('novel_engine.narration_claude.ClaudeNarrator', LocalClaude), \
                 patch('novel_engine.narration_claude.ClaudePlanner', LocalClaudePlanner), redirect_stdout(output):
                code = main(['--campaign', str(self.c.path), '--engine', 'claude', '--claude', 'local',
                             '--json', '--text', 'I carry on with my step.'])
            result = json.loads(output.getvalue())
            self.assertEqual((code, result['status']), (0, 'recorded'), result)
            self.assertEqual(result['narration'], self.c.resume()['session']['scene'])
            self.assertEqual(result['measures']['model'], 'claude-sonnet-5-5')
        calls = [json.loads(s) for s in self.control.with_suffix('.jsonl').read_text().splitlines()]
        self.assertEqual(sum(c['role'] == 'narrator' for c in calls), 11)
        self.assertGreaterEqual(sum(c['role'] == 'planner' for c in calls), 1)


if __name__ == '__main__':
    unittest.main()
