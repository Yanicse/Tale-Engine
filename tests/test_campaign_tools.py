"""`novel unblock` and `novel configure`: explicit, backed-up, previewed, never a turn."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from novel_engine import campaign_tools, cli
from novel_engine.agenda import Agenda, QUIET_NEWS_PREFIX
from novel_engine.adapters import ModelUnavailable
from novel_engine.campaign import Campaign
from novel_engine.demo import configuration, event, initial_state, proposal
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


class QuietNewsSwitchTest(unittest.TestCase):
    POLICY = {'interval_minutes': 360, 'urgent_every': 3, 'instruction': 'Prefer news from the harbor.'}

    def setUp(self):
        base = ROOT / '.tmp-tests'
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name)
        self.c = Campaign(folder / 'campaign.sqlite').create(initial_state(), configuration())
        self.c.open_scene('A guard waits.')
        self.novels = folder / 'novels'
        (self.novels / 'demo').mkdir(parents=True)
        self.write_pack({'title': 'Demo', 'quiet_news': self.POLICY})
        self.now = self.c.state()['world_minute']

    def write_pack(self, novel):
        (self.novels / 'demo' / 'novel.json').write_text(json.dumps(novel), encoding='utf-8')

    def switch(self, value, apply=True):
        return campaign_tools.configure(self.c, apply=apply, quiet_news=value, novels=self.novels)

    def play_at(self, minute, entry=None):
        state = self.c.state()
        updates = []
        if entry:
            updates.append({k: v for k, v in entry.items() if k not in ('revision', 'reference', 'date')})
            updates[0].update(status='finished', expected_revision=entry['revision'], outcome_event_index=0)
        cause = ['agenda:' + entry['entry_id']] if entry else None
        fact = event(state, 'A moment in the story.', [], minute=minute, causes=cause)
        turn = proposal(state, 'A moment in the story.', [fact])
        return self.c.apply(turn, agenda_changes={'updates': updates, 'continuation': None, 'roll_checks': []})

    def pending(self):
        return self.c.world_agenda('narrator')['events']

    def saves(self):
        folder = Path(self.c.path).parent / 'saves'
        return list(folder.iterdir()) if folder.exists() else []

    def test_on_copies_the_pack_policy_and_schedules_nothing_until_a_turn_is_played(self):
        done = self.switch(True)
        self.assertEqual((done['status'], done['mutation'], done['quiet_news']), ('configured', True, self.POLICY))
        self.assertTrue(Path(done['backup']).is_file())
        self.assertEqual(self.c.config()['quiet_news'], self.POLICY)
        with self.c.transaction() as con:
            self.assertFalse(Agenda(con).exists())
        self.assertEqual(self.play_at(self.now + 2)['status'], 'recorded')
        self.assertEqual([e['entry_id'] for e in self.pending()], [QUIET_NEWS_PREFIX + '1'])

    def test_on_again_with_the_same_policy_changes_nothing(self):
        self.switch(True)
        version = self.c.state()['state_version']
        again = self.switch(True)
        self.assertEqual((again['status'], again['mutation']), ('already_configured', False))
        self.assertEqual(self.c.state()['state_version'], version)
        self.assertEqual(len(self.saves()), 1)

    def test_on_needs_a_valid_option_in_the_pack(self):
        for novel in ({'title': 'Demo'}, {'title': 'Demo', 'quiet_news': {'interval_minutes': 5}}):
            self.write_pack(novel)
            with self.assertRaisesRegex(ValueError, 'No valid quiet_news in novels/demo/novel.json: add the option'):
                self.switch(True)
        (self.novels / 'demo' / 'novel.json').unlink()
        with self.assertRaisesRegex(ValueError, 'add the option'):
            self.switch(True)
        self.assertNotIn('quiet_news', self.c.config())

    def test_off_cancels_the_pending_entry_and_the_next_turn_is_not_asked_for_it(self):
        self.switch(True)
        self.play_at(self.now + 2)
        delivered = self.pending()[0]
        self.assertEqual(self.play_at(delivered['due_minute'], delivered)['status'], 'recorded')
        waiting = self.pending()[0]
        self.assertEqual(waiting['entry_id'], QUIET_NEWS_PREFIX + '2')
        done = self.switch(False)
        self.assertEqual((done['status'], done['cancelled_news']), ('configured', [QUIET_NEWS_PREFIX + '2']))
        self.assertNotIn('quiet_news', self.c.config())
        self.assertEqual(self.pending(), [])
        with self.c.transaction() as con:
            agenda = Agenda(con)
            self.assertEqual(agenda.get(QUIET_NEWS_PREFIX + '2')['status'], 'cancelled')
            self.assertEqual(agenda.get(QUIET_NEWS_PREFIX + '1')['status'], 'finished')
            history = con.execute('SELECT revision,request_id FROM agenda_history WHERE entry_id=? ORDER BY revision',
                                  (QUIET_NEWS_PREFIX + '2',)).fetchall()
        self.assertEqual([tuple(row) for row in history], [(0, history[0][1]), (1, 'configure:quiet_news_off')])
        # Past the old deadline, the turn is recorded: nothing is forgotten and nothing new is scheduled.
        self.assertEqual(self.play_at(waiting['due_minute'] + 5)['status'], 'recorded')
        self.assertEqual(self.pending(), [])

    def test_off_when_already_off_changes_nothing(self):
        done = self.switch(False)
        self.assertEqual((done['status'], done['mutation'], done['cancelled_news']), ('already_configured', False, []))
        self.assertEqual(self.saves(), [])

    def test_a_dry_run_writes_nothing(self):
        self.switch(True, apply=False)
        self.assertNotIn('quiet_news', self.c.config())
        self.assertEqual(self.saves(), [])
        self.switch(True)
        self.play_at(self.now + 2)
        preview = self.switch(False, apply=False)
        self.assertEqual((preview['status'], preview['mutation'], preview['cancelled_news']),
                         ('preview', False, [QUIET_NEWS_PREFIX + '1']))
        self.assertEqual(self.c.config()['quiet_news'], self.POLICY)
        self.assertEqual([e['entry_id'] for e in self.pending()], [QUIET_NEWS_PREFIX + '1'])
        self.assertEqual(len(self.saves()), 1)

    def test_the_switch_is_reachable_from_the_novel_cli(self):
        def run(*words):
            out = io.StringIO()
            with redirect_stdout(out):
                cli.main(['configure', '--campaign', str(self.c.path), *words])
            return json.loads(out.getvalue())
        with mock.patch.object(cli, 'NOVELS', self.novels):
            self.assertEqual(run('--quiet-news', 'on', '--apply')['quiet_news'], self.POLICY)
            self.write_pack({'title': 'Demo'})
            self.assertEqual(run('--quiet-news', 'off', '--apply')['status'], 'configured')
            with self.assertRaises(SystemExit) as stop:
                run('--quiet-news', 'on')
            self.assertIn('add the option', stop.exception.code)


if __name__ == '__main__':
    unittest.main()
