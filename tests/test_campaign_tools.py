"""`novel unblock` and `novel configure`: explicit, backed-up, previewed, never a turn."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from novel_engine import campaign_tools, cli
from novel_engine.adapters import ModelUnavailable
from novel_engine.campaign import Campaign
from novel_engine.demo import configuration, initial_state
from novel_engine.narrative import NarrativeLoop

ANSWER = {'narrative_schema_version': '1.2', 'narration': 'The guard nods.', 'events': [],
          'blocking_questions': [], 'memory_request': None, 'memory_updates': [], 'agenda_updates': [],
          'continuation': None, 'roll_checks': []}


class Narrator:
    def __init__(self):
        self.calls, self.fail, self.last_metrics = 0, 0, {}

    def generate(self, prompt):
        self.calls += 1
        if self.fail:
            self.fail -= 1
            raise ModelUnavailable('HTTP 503 unavailable')
        return dict(ANSWER)


class CampaignToolsTest(unittest.TestCase):
    def setUp(self):
        base = ROOT / '.tmp-tests'
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        self.c = Campaign(Path(self.temp.name) / 'campaign.sqlite').create(initial_state(), configuration())
        self.c.open_scene('A guard waits.')
        self.narrator = Narrator()
        self.loop = NarrativeLoop(self.c, self.narrator)

    def exhaust(self):
        self.narrator.fail = 2
        for attempt in range(2):
            with self.assertRaises(ModelUnavailable):
                self.loop.run_turn('I greet the guard.') if attempt == 0 else self.loop.run_turn(resume=True, retry=True)
        blocked = self.loop.run_turn(resume=True, retry=True)
        self.assertEqual(blocked['diagnostic_code'], 'recovery_blocked')
        self.assertIn('novel unblock', blocked['reason'])
        return blocked

    def test_two_outages_block_the_action_until_the_user_grants_one_more_generation(self):
        self.exhaust()
        calls, version = self.narrator.calls, self.c.state()['state_version']
        preview = campaign_tools.unblock(self.c)
        self.assertEqual((preview['status'], preview['mutation']), ('preview', False))
        self.assertEqual(preview['counts']['generations'], 2)
        # A preview changes nothing: the action is still blocked.
        self.assertEqual(self.loop.run_turn(resume=True, retry=True)['diagnostic_code'], 'recovery_blocked')
        granted = campaign_tools.unblock(self.c, apply=True)
        self.assertEqual(granted['status'], 'granted')
        self.assertTrue(Path(granted['backup']).is_file())
        self.assertEqual(self.narrator.calls, calls)
        self.assertEqual(self.c.state()['state_version'], version)
        result = self.loop.run_turn(resume=True, retry=True)
        self.assertEqual((result['status'], result['narration']), ('recorded', 'The guard nods.'))
        self.assertEqual(self.c.state()['state_version'], version + 1)

    def test_a_grant_is_bounded_to_one_generation_and_repeated_by_hand_only(self):
        self.exhaust()
        campaign_tools.unblock(self.c, apply=True)
        self.assertEqual(campaign_tools.unblock(self.c, apply=True)['status'], 'nothing_to_unblock')
        self.narrator.fail = 1
        with self.assertRaises(ModelUnavailable):
            self.loop.run_turn(resume=True, retry=True)
        self.assertEqual(self.loop.run_turn(resume=True, retry=True)['diagnostic_code'], 'recovery_blocked')
        self.assertEqual(campaign_tools.unblock(self.c, apply=True)['status'], 'granted')
        self.assertEqual(self.loop.run_turn(resume=True, retry=True)['status'], 'recorded')

    def test_nothing_to_unblock_without_a_pending_or_exhausted_action(self):
        self.assertEqual(campaign_tools.unblock(self.c, apply=True)['status'], 'nothing_to_unblock')
        self.narrator.fail = 1
        with self.assertRaises(ModelUnavailable):
            self.loop.run_turn('I greet the guard.')
        report = campaign_tools.unblock(self.c, apply=True)
        self.assertEqual(report['status'], 'nothing_to_unblock')
        self.assertIn('--resume --retry', report['reason'])

    def test_configure_previews_applies_and_backs_up(self):
        preview = campaign_tools.configure(self.c, creations=True, bounded_recovery=True)
        self.assertEqual((preview['status'], preview['mutation']), ('preview', False))
        self.assertIsNone(self.c.config().get('creation_format'))
        done = campaign_tools.configure(self.c, apply=True, creations=True, bounded_recovery=True)
        self.assertEqual(done['status'], 'configured')
        self.assertTrue(Path(done['backup']).is_file())
        self.assertEqual((self.c.config()['creation_format'], self.c.config()['recovery_policy']), ('1', 'bounded_v1'))
        self.assertEqual(campaign_tools.configure(self.c, apply=True, creations=True)['status'], 'already_configured')
        campaign_tools.configure(self.c, apply=True, creations=False, bounded_recovery=False)
        self.assertIsNone(self.c.config()['creation_format'])
        self.assertIsNone(self.c.config()['recovery_policy'])

    def test_commands_are_reachable_from_the_novel_cli(self):
        self.exhaust()
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cli.main(['unblock', '--campaign', str(self.c.path)]), 0)
        self.assertEqual(json.loads(out.getvalue())['status'], 'preview')
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(['unblock', '--campaign', str(self.c.path), '--apply'])
        self.assertEqual(json.loads(out.getvalue())['status'], 'granted')
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(['configure', '--campaign', str(self.c.path), '--creations', 'on', '--apply'])
        self.assertEqual(json.loads(out.getvalue())['creation_format'], '1')


if __name__ == '__main__':
    unittest.main()
