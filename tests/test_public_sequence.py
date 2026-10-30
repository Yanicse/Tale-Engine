"""A whole session through the public entry point (`play_narrative.main`) on a campaign made from a pack.

The model is a local CLI that answers deterministically; everything else is real: the pack, `novel campaign new`, the
command line, the validation, the SQLite save, the planner, the second process and the restart.
"""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from novel_engine import cli, resume
from novel_engine.adapters import ModelUnavailable
from novel_engine.agenda import Agenda
from novel_engine.campaign import Campaign
from novel_engine.contracts import ROOT as HOME
from novel_engine.narrative import NarrativeLoop, narrative_lock
from novel_engine.scripts.play_narrative import main
from test_loop_audit import LocalNarrator, LocalPlanner

WORKER = ROOT / 'tests' / 'process_worker.py'


class SessionFixture(unittest.TestCase):
    slug = 'publicsequence'

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='.tmp-session-', dir=HOME))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.target = HOME / 'campaigns' / self.slug
        self.addCleanup(shutil.rmtree, self.target, True)
        self.active = resume.ACTIVE
        self.before = self.active.read_text(encoding='utf-8') if self.active.is_file() else None
        self.addCleanup(self.restore_active)
        with mock.patch.object(cli, 'NOVELS', self.tmp), redirect_stdout(io.StringIO()):
            cli.main(['example', '--slug', self.slug])
        self.path = self.target / 'campaign.sqlite'
        self.c = Campaign(self.path)
        self.control = self.tmp / 'control.txt'
        LocalNarrator.control = LocalPlanner.control = self.control

    def restore_active(self):
        if self.before is None:
            self.active.unlink(missing_ok=True)
        else:
            self.active.write_text(self.before, encoding='utf-8')

    def cli(self, *args, expect=0):
        output = io.StringIO()
        with mock.patch('novel_engine.narration_codex.CodexNarrator', LocalNarrator), \
                mock.patch('novel_engine.planner.CodexPlanner', LocalPlanner), redirect_stdout(output):
            code = main(['--campaign', str(self.path), '--codex', 'local', '--engine', 'codex', '--json', *args])
        result = json.loads(output.getvalue())
        self.assertEqual(code, expect, result)
        return result

    def step(self, mode, text):
        self.control.write_text(mode, encoding='utf-8')
        result = self.cli('--text', text)
        self.assertEqual(result['status'], 'recorded', result)
        self.assertEqual(result['narration'], Campaign(self.path).resume()['session']['scene'])
        return result

    def calls(self):
        log = self.control.with_suffix('.jsonl')
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def resources(self):
        return Campaign(self.path).state()['resources']


class TwentyActionSession(SessionFixture):
    def test_a_pack_session_of_twenty_actions_with_creations_mechanics_agenda_and_restart(self):
        start = self.resources()
        self.step('npc_creation', 'Who knows the trails?')
        self.step('offer_creation', 'What mission do you offer?')
        # An offer is neither an acceptance nor a gain.
        self.assertEqual(self.resources(), start)
        self.step('creation_accept', 'I accept the mission.')
        self.step('site_arrival', 'I walk to the market.')
        self.assertEqual(Campaign(self.path).state()['positions']['player'], 'market')
        self.step('site_purchase', 'I buy some wood.')
        self.assertEqual(self.resources()['player_silver'], start['player_silver'] - 3)
        self.step('site_spell', 'I cast Second wind.')
        self.assertEqual(self.resources()['player_mana'], start['player_mana'] - 9)
        minute = Campaign(self.path).state()['world_minute']
        self.step('site_recovery', 'I wait a minute.')
        self.assertEqual(Campaign(self.path).state()['world_minute'], minute + 1)
        # Time passing restores neither health nor mana.
        self.assertEqual(self.resources()['player_mana'], start['player_mana'] - 9)
        self.assertEqual(self.resources()['player_hp'], start['player_hp'])
        with Campaign(self.path).transaction() as con:
            now = json.loads(con.execute('SELECT state FROM campaign').fetchone()[0])['world_minute']
            Agenda(con)._put({'entry_id': 'test:messenger', 'expected_revision': -1, 'title': 'Messenger',
                              'actor': 'messenger', 'location': 'market', 'kind': 'verification', 'due_minute': now + 1,
                              'conditions': [], 'references': ['pack:character_sheet'], 'audience': ['narrator', 'player'],
                              'interrupt': True, 'status': 'planned', 'reason': 'Expected arrival.',
                              'outcome_event_index': None}, 'fixture', now)
        self.step('site_due', 'I wait for the messenger.')
        for i in range(11):
            self.step('site_dialogue', 'I talk about the weather %d.' % i)
        self.c = Campaign(self.path)
        reminder = self.step('reminder_creation', 'Remind me of Lina and the bridge mission.')
        self.assertIn('report', reminder['narration'])
        calls = self.calls()
        self.assertEqual(sum(call['role'] == 'narrator' for call in calls), 20)
        self.assertGreaterEqual(sum(call['role'] == 'planner' for call in calls), 1)
        state = Campaign(self.path).state()
        self.assertEqual(state['resources']['player_silver'], start['player_silver'] - 3)
        self.assertEqual(state['inventory']['wood']['quantity'], 1)
        # The planner summary written during the session is shown by the local command, with no model call.
        calls_before = len(self.calls())
        out = io.StringIO()
        with redirect_stdout(out):
            main(['--campaign', str(self.path), '--summaries'])
        self.assertIn('[summary:', out.getvalue())
        self.assertEqual(len(self.calls()), calls_before)

    def test_a_refused_spell_changes_nothing_and_the_replay_spends_once(self):
        start = self.resources()
        self.control.write_text('site_overspend', encoding='utf-8')
        result = self.cli('--text', 'I cast second wind four times.', expect=2)
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(self.resources(), start)
        self.assertTrue(Campaign(self.path).resume()['session']['pending_action'])
        self.control.write_text('site_spell', encoding='utf-8')
        done = self.cli('--reevaluate')
        self.assertEqual(done['status'], 'recorded')
        self.assertEqual(self.resources()['player_mana'], start['player_mana'] - 9)
        again = self.cli('--resume')
        self.assertEqual(self.resources()['player_mana'], start['player_mana'] - 9)
        self.assertEqual(again['narration'], done['narration'])


class TwoProcessTests(SessionFixture):
    def test_a_second_process_cannot_narrate_while_one_holds_the_campaign(self):
        with narrative_lock(self.c):
            result = subprocess.run([sys.executable, str(WORKER), 'lock', str(self.path)],
                                    capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), 'locked')
        result = subprocess.run([sys.executable, str(WORKER), 'lock', str(self.path)],
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), 'free')

    def test_a_write_by_another_process_during_generation_rejects_the_turn_and_keeps_the_action(self):
        class Narrator:
            calls, last_metrics = 0, {}

            def generate(inner, prompt):
                inner.calls += 1
                done = subprocess.run([sys.executable, str(WORKER), 'write', str(self.path)],
                                      capture_output=True, text=True, check=True)
                self.assertEqual(done.stdout.strip(), 'recorded')
                return {'narrative_schema_version': '1.3', 'narration': 'Too late.', 'events': [],
                        'blocking_questions': [], 'memory_request': None, 'memory_updates': [],
                        'agenda_updates': [], 'continuation': None, 'roll_checks': [], 'creation_schema_version': '1',
                        'creation_updates': []}
        narrator = Narrator()
        result = NarrativeLoop(self.c, narrator).run_turn('Hello')
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(result['narration'], '')
        self.assertEqual(narrator.calls, 1)
        self.assertEqual(self.c.state()['state_version'], 1)
        self.assertTrue(self.c.resume()['session']['pending_action'])
        with self.assertRaises(ModelUnavailable):
            NarrativeLoop(self.c, narrator).run_turn(resume=True)


if __name__ == '__main__':
    unittest.main()
