"""CLI errors kept, masked and told apart, with no remote calls."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from novel_engine.adapters import ModelUnavailable
from novel_engine.diagnostic_cli import safe_detail,collect_failure,failure_summary
from novel_engine.narration_codex import CodexNarrator
from novel_engine.narrative import NarrativeLoop
from novel_engine.campaign import Campaign
from novel_engine.demo import initial_state,configuration
from novel_engine.planner import review_world,CodexPlanner


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        base=ROOT/'.tmp-tests';base.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=base)
        self.c=Campaign(Path(self.temp.name)/'novel_engine.sqlite').create(initial_state(),configuration())
        self.c.open_scene('The guard is present.')

    def tearDown(self):self.temp.cleanup()

    def process(self,events=(),stderr=b'',code=1):
        process=Mock();process.returncode=code
        process.communicate.return_value=('\n'.join(json.dumps(e) for e in events).encode(),stderr)
        return process

    def test_nonzero_exit_preserves_structured_503_not_false_login_advice(self):
        process=self.process([{'type':'turn.failed','error':{'message':'Unexpected status 503 Service Unavailable','request_id':'req-test'}}])
        adapter=CodexNarrator(executable='codex.exe')
        with patch('novel_engine.narration_codex.subprocess.Popen',return_value=process):
            with self.assertRaisesRegex(ModelUnavailable,'HTTP 503'):adapter.generate('Test technique.')
        self.assertEqual(adapter.last_error['category'],'service')
        self.assertEqual(adapter.last_error['exit_code'],1)
        self.assertNotIn('login',failure_summary(adapter.last_error))

    def test_failed_event_on_zero_exit_still_keeps_error(self):
        process=self.process([{'type':'error','message':'Websocket closed before completion'}],code=0)
        adapter=CodexNarrator(executable='codex.exe')
        with patch('novel_engine.narration_codex.subprocess.Popen',return_value=process):
            with self.assertRaisesRegex(ModelUnavailable,'network'):adapter.generate('Test technique.')
        self.assertEqual(adapter.last_error['category'],'network')

    def test_stderr_permission_failure_is_distinguished(self):
        process=self.process(stderr='Error: Access denied. (os error 5)'.encode())
        adapter=CodexNarrator(executable='codex.exe')
        with patch('novel_engine.narration_codex.subprocess.Popen',return_value=process):
            with self.assertRaisesRegex(ModelUnavailable,'Local'):adapter.generate('Test technique.')
        self.assertEqual(adapter.last_error['category'],'permissions')

    def test_schema_and_arguments_and_limits_are_distinguished(self):
        for text,kind in [('HTTP 400 invalid_json_schema for response_format','schema'),
                          ("error: unexpected argument '--unknown' found",'arguments'),
                          ('HTTP 429 Too many requests','rate_limit'),
                          ('Your usage limit has been reached','usage_limit'),
                          ('code=server_error; service unavailable','service'),
                          ('Unexpected status 401 Unauthorized','authentication')]:
            error=collect_failure(b'',text,1)
            self.assertEqual(error['category'],kind,text)

    def test_tokens_and_cookies_are_masked_and_punctuation_kept(self):
        raw='Authorization: Bearer secrettoken Cookie: session-secret\n'
        raw+='{"access_token":"secret-access", "refresh_token":"secret-refresh", "password":"secret-pass"}\n'
        raw+='api_key=sk-abcdefghijklmnop JWT=eyJheader.eyJpayload.signature'+chr(0x2014)
        safe=safe_detail(raw)
        for secret in ('secrettoken','session-secret','secret-access','secret-refresh','secret-pass','sk-abcdefghijklmnop','eyJheader.eyJpayload.signature'):
            self.assertNotIn(secret,safe)
        self.assertIn(chr(0x2014),safe)
        self.assertLessEqual(len(safe_detail('x'*10000)),4000)

    def test_error_does_not_copy_request_body_or_narration(self):
        out=json.dumps({'type':'turn.failed','error':{'message':'HTTP 503 unavailable','body':{'prompt':'SECRET CONTEXT'}}})
        out+='\n'+json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'SECRET NARRATION'}})
        result=collect_failure(out,'',1)
        self.assertNotIn('SECRET',json.dumps(result))

    def test_warning_plus_final_message_is_not_a_failure(self):
        response={'narrative_schema_version':'1.2','narration':'Hello.','events':[], 'blocking_questions':[], 'memory_request':None,'memory_updates':[], 'agenda_updates':[], 'continuation':None,'roll_checks':[]}
        process=self.process([{'type':'item.completed','item':{'type':'error','message':'Avertissement local'}},
                              {'type':'item.completed','item':{'type':'agent_message','text':json.dumps(response)}}],
                             stderr=b'WARNING: optional cleanup failed',code=0)
        adapter=CodexNarrator(executable='codex.exe')
        with patch('novel_engine.narration_codex.subprocess.Popen',return_value=process):
            self.assertEqual(adapter.generate('Test technique.'),response)
        self.assertIsNone(adapter.last_error)
        self.assertEqual(adapter.last_metrics['cli_warning_count'],1)

    def test_narrative_failure_is_saved_without_changing_state_or_retrying(self):
        process=self.process([{'type':'turn.failed','error':{'message':'HTTP 503 unavailable'}}])
        adapter=CodexNarrator(executable='codex.exe');loop=NarrativeLoop(self.c,adapter)
        before=self.c.state()
        with patch('novel_engine.narration_codex.subprocess.Popen',return_value=process) as launch:
            with self.assertRaisesRegex(ModelUnavailable,'503'):loop.run_turn('Hello.')
            with self.assertRaises(ModelUnavailable):loop.run_turn(resume=True)
        self.assertEqual(launch.call_count,1)
        self.assertEqual(self.c.state(),before)
        self.assertEqual(self.c.resume()['session']['pending_action']['text'],'Hello.')
        with self.c.transaction() as con:
            metrics=json.loads(con.execute('SELECT metrics FROM narrative_attempts').fetchone()[0])
            self.assertEqual(metrics['cli_error']['category'],'service')
            diagnostic=json.loads(con.execute('SELECT payload FROM narrative_diagnostics').fetchone()[0])
            self.assertEqual(diagnostic['response']['cli_error']['http_status'],503)

    def test_planner_failure_keeps_the_same_diagnostics(self):
        try:
            from test_planner import install_test_chronicle
        except ImportError:
            self.skipTest('test_planner not yet ported to the public repository')
        prepared=self.c.prepare_turn('Hello.',narrative_memory=True)
        with self.c.transaction() as con:
            install_test_chronicle(con)
        adapter=CodexPlanner(executable='codex.exe')
        process=self.process([{'type':'turn.failed','error':{'message':'Unexpected status 429 Too many requests'}}])
        before=self.c.state()
        with patch('novel_engine.narration_codex.subprocess.Popen',return_value=process):
            with self.assertRaises(ModelUnavailable):review_world(self.c,adapter,prepared)
        self.assertEqual(self.c.state(),before)
        with self.c.transaction() as con:
            row=con.execute('SELECT metrics,response FROM planner_reviews').fetchone()
            self.assertEqual(json.loads(row['metrics'])['cli_error']['category'],'rate_limit')
            self.assertEqual(json.loads(row['response'])['cli_error']['http_status'],429)

    def test_empty_failure_keeps_exit_code_and_does_not_guess_reason(self):
        error=collect_failure(b'',b'',7)
        self.assertEqual(error['category'],'unknown')
        self.assertIn('exit code 7',failure_summary(error))

    def test_timeout_keeps_partial_diagnostics_and_never_retries(self):
        process=self.process(code=-9);process.pid=999999
        process.communicate.side_effect=[subprocess.TimeoutExpired('codex',1),
                                         (b'',b'connection attempt timed out')]
        adapter=CodexNarrator(executable='codex.exe',timeout=1)
        with patch('novel_engine.narration_codex.subprocess.Popen',return_value=process) as launch, \
             patch('novel_engine.narration_codex.subprocess.run'):
            with self.assertRaisesRegex(ModelUnavailable,'timeout'):adapter.generate('Test technique.')
        self.assertEqual(launch.call_count,1)
        self.assertEqual(adapter.last_error['category'],'timeout')
        self.assertIn('timed out',adapter.last_error['stderr_tail'])

    def test_timeout_skill_warning_is_preserved_separately_from_cause(self):
        warning='Exceeded skills context budget. All skill descriptions were removed.'
        events=[{'type':'thread.started','thread_id':'test'}, {'type':'turn.started'},
                {'type':'item.completed','item':{'type':'error','message':warning}}]
        process=self.process(events,code=1);process.pid=999999
        partial=process.communicate.return_value
        process.communicate.side_effect=[subprocess.TimeoutExpired('codex',1),partial]
        adapter=CodexNarrator(executable='codex.exe',timeout=1)
        with patch('novel_engine.narration_codex.subprocess.Popen',return_value=process) as launch, \
             patch('novel_engine.narration_codex.subprocess.run'):
            with self.assertRaises(ModelUnavailable) as caught:adapter.generate('Test technique.')
        self.assertEqual(launch.call_count,1)
        self.assertEqual(adapter.last_error['warnings'],[warning])
        self.assertEqual(adapter.last_error['detail'],'')
        self.assertEqual(adapter.last_error['events'],[])
        self.assertEqual(adapter.last_error['progress_events'],['thread.started','turn.started'])
        self.assertIn('Limit: 1 seconds',str(caught.exception))
        self.assertNotIn('skills',str(caught.exception))
        self.assertIn('No precise cause',str(caught.exception))

    def test_skill_warning_does_not_hide_real_plain_stderr_error(self):
        warning='Exceeded skills context budget. All skill descriptions were removed.'
        error=collect_failure('',warning+'\nHTTP 503 Service Unavailable',1)
        self.assertEqual(error['category'],'service')
        self.assertEqual(error['warnings'],[warning])
        self.assertNotIn('skills',failure_summary(error))
        self.assertIn('503',failure_summary(error))

    def test_warning_only_nonzero_exit_keeps_unknown_cause(self):
        warning='Exceeded skills context budget.'
        error=collect_failure('',warning,1)
        self.assertEqual(error['category'],'unknown')
        self.assertIn('exit code 1',failure_summary(error))
        self.assertNotIn('skills',failure_summary(error))

    def test_narrative_default_timeout_and_explicit_override(self):
        self.assertEqual(CodexNarrator(executable='codex.exe').timeout,240)
        self.assertEqual(CodexNarrator(executable='codex.exe',timeout=60).timeout,60)

    def test_all_cookie_values_are_masked(self):
        result=safe_detail('Cookie: session=secret-session; csrf=secret-csrf\nHTTP 503 unavailable')
        self.assertNotIn('secret-session',result);self.assertNotIn('secret-csrf',result)
        self.assertIn('503',result)

    def test_new_success_clears_previous_error(self):
        adapter=CodexNarrator(executable='codex.exe')
        failure=self.process(stderr=b'HTTP 503 unavailable')
        response={'narrative_schema_version':'1.2','narration':'Hello.','events':[], 'blocking_questions':[], 'memory_request':None,'memory_updates':[], 'agenda_updates':[], 'continuation':None,'roll_checks':[]}
        success=self.process([{'type':'item.completed','item':{'type':'agent_message','text':json.dumps(response)}}],code=0)
        with patch('novel_engine.narration_codex.subprocess.Popen',side_effect=[failure,success]):
            with self.assertRaises(ModelUnavailable):adapter.generate('Test technique.')
            self.assertEqual(adapter.generate('Another test explicitly requested.'),response)
        self.assertIsNone(adapter.last_error)
        self.assertNotIn('cli_error',adapter.last_metrics)


if __name__=='__main__':unittest.main(verbosity=2)
