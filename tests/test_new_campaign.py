"""What a campaign created from a pack really has: creations, summaries, chronicler and arcs when the pack asks."""
from contextlib import closing, redirect_stderr, redirect_stdout
import argparse
import io
import json
import shutil
import sqlite3
import unittest
from unittest import mock

from novel_engine import cli, world_setup
from novel_engine.arcs import ArcRegistry
from novel_engine.pack_check import check_pack
from novel_engine.planner import WorldPlanner
from home import isolated_home


class Fixture(unittest.TestCase):
    slug = 'newcampaigntest'

    def setUp(self):
        self.home = isolated_home(self)
        self.tmp = self.home / 'novels'
        self.target = self.home / 'campaigns' / self.slug
        patch = mock.patch.object(cli, 'NOVELS', self.tmp)
        patch.start()
        self.addCleanup(patch.stop)

    def quiet(self, *argv):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return cli.main(list(argv))

    def database(self):
        return closing(sqlite3.connect(self.target / 'campaign.sqlite'))

    def tables(self):
        with self.database() as con:
            return {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def config(self):
        with self.database() as con:
            return json.loads(con.execute('SELECT config FROM campaign').fetchone()[0])


class DefaultCampaignTests(Fixture):
    def test_a_new_campaign_keeps_creations_and_installs_the_planner_but_not_the_chronicler(self):
        self.quiet('example', '--slug', self.slug)
        config = self.config()
        self.assertEqual(config['creation_format'], '1')
        self.assertIsNone(config.get('recovery_policy'))
        tables = self.tables()
        self.assertIn('planner_meta', tables)
        self.assertIn('planner_summaries', tables)
        self.assertNotIn('chronicle_clocks', tables)
        self.assertNotIn('chronicle_arcs', tables)

    def test_flags_turn_creations_off_and_bounded_recovery_on(self):
        self.quiet('example', '--slug', self.slug)
        shutil.rmtree(self.target)
        with redirect_stdout(io.StringIO()):
            cli.campaign_new(argparse.Namespace(novel=self.slug, start='start', no_creations=True, bounded_recovery=True))
        config = self.config()
        self.assertIsNone(config['creation_format'])
        self.assertEqual(config['recovery_policy'], 'bounded_v1')

    def test_the_example_world_installs_the_chronicler_and_the_arcs_from_the_pack(self):
        self.quiet('example', '--slug', self.slug, '--world')
        with self.database() as con:
            con.row_factory = sqlite3.Row
            planner = WorldPlanner(con)
            self.assertEqual([c['id'] for c in planner.clocks()], ['harbor_fund', 'lens_sale'])
            self.assertEqual({n['id'] for n in planner.chronology_nodes()},
                             {'festival', 'lens_theft', 'fog_inspection'})
            self.assertEqual([a['id'] for a in ArcRegistry(con).entries()], ['empty_chest', 'missing_lens'])
        # The campaign keeps its own copy: editing the pack later changes nothing for it.
        copy = self.target / 'chronology.json'
        self.assertTrue(copy.is_file())
        (self.tmp / self.slug / 'chronology.json').write_text('{"nodes": []}', encoding='utf-8')
        with self.database() as con:
            con.row_factory = sqlite3.Row
            self.assertEqual(len(WorldPlanner(con).chronology_nodes()), 3)

    def test_the_default_example_has_no_world_files(self):
        self.quiet('example', '--slug', self.slug)
        self.assertFalse(world_setup.present(self.tmp / self.slug))


class CanonSelectionTests(Fixture):
    """The canon a pack builds must reach the narrator of a campaign created by the command line."""

    def answer(self, folder, causes):
        events = [{'world_minute': 5, 'world_second': 0, 'description': 'Tamsin answers about the lantern.',
                   'causes': causes, 'conditions': [], 'changes': [], 'audience': ['narrator', 'player'],
                   'mechanical_operations': [], 'public_consequences': [], 'asset_operations': []}]
        answer = {'narrative_schema_version': '1.3', 'narration': 'Tamsin points at the green light.',
                  'events': events, 'blocking_questions': [], 'memory_request': None, 'memory_updates': [],
                  'agenda_updates': [], 'continuation': None, 'roll_checks': [], 'creation_schema_version': '1',
                  'creation_updates': []}
        (folder / 'turn.response.json').write_text(json.dumps(answer), encoding='utf-8')

    def test_a_new_campaign_selects_sourced_claims_and_accepts_them_as_causes(self):
        from novel_engine.adapters import ModelUnavailable
        from novel_engine.campaign import Campaign
        from novel_engine.narration_manual import ManualNarrator
        from novel_engine.narrative import NarrativeLoop
        self.quiet('example', '--slug', self.slug)
        self.assertEqual(self.config()['canon_selection'], {'version': 1, 'enabled': True})
        campaign = Campaign(self.target / 'campaign.sqlite')
        folder = self.tmp / 'manual'
        loop = NarrativeLoop(campaign, ManualNarrator(folder))
        with self.assertRaises(ModelUnavailable):
            loop.run_turn('I ask Tamsin Reed who keeps the Salt Lantern in the Gull Tower.')
        prompt = (folder / 'turn.prompt.md').read_text(encoding='utf-8')
        packet = json.loads(prompt.split('CURRENT TURN\n', 1)[1].split('\n\n# Answer format', 1)[0])
        facts = packet['documentary_canon']['facts']
        self.assertTrue(facts)
        self.assertTrue({'Salt Lantern', 'Tamsin Reed', 'Gull Tower'} & {fact['subject'] for fact in facts})
        reference = facts[0]['reference']
        self.assertIn(reference, packet['allowed_references'])
        self.answer(folder, ['current_action', reference])
        self.assertEqual(loop.run_turn(resume=True, retry=True)['status'], 'recorded')

    def test_configure_turns_the_canon_selection_of_an_older_campaign_on(self):
        from novel_engine import campaign_tools
        from novel_engine.campaign import Campaign
        self.quiet('example', '--slug', self.slug)
        campaign = Campaign(self.target / 'campaign.sqlite')
        self.assertEqual(campaign_tools.configure(campaign, apply=True, canon_selection=False)['canon_selection'], False)
        self.assertFalse(self.config()['canon_selection']['enabled'])
        out = io.StringIO()
        with redirect_stdout(out):
            cli.main(['configure', '--campaign', str(campaign.path), '--canon-selection', 'on', '--apply'])
        self.assertTrue(json.loads(out.getvalue())['canon_selection'])
        self.assertTrue(self.config()['canon_selection']['enabled'])


class WorldFileValidationTests(Fixture):
    def pack(self):
        self.quiet('example', '--slug', self.slug, '--world')
        shutil.rmtree(self.target)
        return self.tmp / self.slug

    def edit(self, pack, name, change):
        path = pack / name
        data = json.loads(path.read_text(encoding='utf-8'))
        change(data)
        path.write_text(json.dumps(data), encoding='utf-8')

    def test_a_valid_world_pack_has_no_error(self):
        report = check_pack(self.pack())
        self.assertEqual(report.errors, [])

    def test_a_clock_citing_an_unknown_episode_is_reported_with_its_file_and_blocks_campaign_new(self):
        pack = self.pack()
        self.edit(pack, 'clocks.json', lambda clocks: clocks[0].update(references=['chronology:missing']))
        report = check_pack(pack)
        self.assertTrue(any(e.startswith('clocks.json') for e in report.errors), report.errors)
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            cli.campaign_new(argparse.Namespace(novel=self.slug, start='start'))
        self.assertFalse((self.target / 'campaign.sqlite').exists())

    def test_a_full_initial_clock_and_a_bad_json_file_are_reported(self):
        pack = self.pack()
        self.edit(pack, 'clocks.json', lambda clocks: clocks[0].update(progression=4, status='burst'))
        self.assertTrue(any(e.startswith('clocks.json') for e in check_pack(pack).errors))
        (pack / 'clocks.json').write_text('[not json', encoding='utf-8')
        self.assertTrue(any('not valid JSON' in e for e in check_pack(pack).errors))

    def test_arcs_must_cover_every_episode_and_need_a_chronology(self):
        pack = self.pack()
        self.edit(pack, 'arcs.json', lambda plan: plan['arcs'].pop())
        self.assertTrue(any(e.startswith('arcs.json') and 'without follow-up' in e for e in check_pack(pack).errors))
        (pack / 'chronology.json').unlink()
        self.assertTrue(any(e.startswith('arcs.json') for e in check_pack(pack).errors))

    def test_clocks_alone_install_the_chronicler_with_an_empty_chronology(self):
        pack = self.pack()
        (pack / 'chronology.json').unlink()
        (pack / 'arcs.json').unlink()
        self.edit(pack, 'clocks.json', lambda clocks: [c.update(origin='invention', references=['pack:clocks']) for c in clocks])
        self.assertEqual(check_pack(pack).errors, [])
        self.quiet('campaign', 'new', self.slug)
        with self.database() as con:
            con.row_factory = sqlite3.Row
            self.assertEqual(len(WorldPlanner(con).clocks()), 2)
            self.assertFalse(ArcRegistry(con).exists())

    def fresh(self):
        shutil.rmtree(self.tmp / self.slug, True)
        return self.pack()

    def test_chronology_edges_are_checked_and_campaign_new_refuses_them_without_a_traceback(self):
        nodes = json.loads((self.pack() / 'chronology.json').read_text(encoding='utf-8'))['nodes']
        node, other = nodes[0]['id'], nodes[1]['id']
        cases = {
            'missing after': ([{'before': node}], "chronology.json: edges[0] needs 'before' and 'after', two node ids"),
            'not a list': ('zzz', 'chronology.json: "edges" must be a list'),
            'unknown node': ([{'before': node, 'after': 'nowhere'}],
                             'chronology.json: edges[0].after names an unknown node: nowhere'),
            'cycle': ([{'before': node, 'after': other}, {'before': other, 'after': node}],
                      'chronology.json: edges form a cycle'),
            'self loop': ([{'before': node, 'after': node}], 'chronology.json: edges form a cycle'),
        }
        for label, (edges, message) in cases.items():
            with self.subTest(label):
                pack = self.fresh()
                self.edit(pack, 'chronology.json', lambda data: data.update(edges=edges))
                self.assertTrue(any(e.startswith(message) for e in check_pack(pack).errors), check_pack(pack).errors)
                with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                    cli.campaign_new(argparse.Namespace(novel=self.slug, start='start'))
                self.assertFalse((self.target / 'campaign.sqlite').exists())
                with self.assertRaises(ValueError):
                    world_setup.load(pack)

    def test_a_chronology_with_valid_edges_still_installs(self):
        pack = self.fresh()
        ids = [n['id'] for n in json.loads((pack / 'chronology.json').read_text(encoding='utf-8'))['nodes']]
        self.edit(pack, 'chronology.json', lambda data: data.update(edges=[{'before': ids[0], 'after': ids[1]}]))
        self.assertEqual(check_pack(pack).errors, [])

    def test_malformed_arcs_give_one_readable_line_and_never_a_traceback(self):
        def arc(change):
            return lambda plan: change(plan['arcs'][0])
        cases = {
            'outside_arcs list': (lambda plan: plan.update(outside_arcs=[]),
                                  'arcs.json: outside_arcs must be an object'),
            'dependencies null': (arc(lambda a: a.update(dependencies=None)),
                                  'arcs.json: arcs[0].dependencies must be a list of arc ids'),
            'end_days text': (arc(lambda a: a.update(end_days='20')),
                              'arcs.json: arcs[0].end_days must be a whole number of days'),
            'steps text': (arc(lambda a: a.update(steps='abc')),
                           'arcs.json: arcs[0].steps must be a list of texts'),
            'opening_days bool': (arc(lambda a: a.update(opening_days=True)),
                                  'arcs.json: arcs[0].opening_days must be a whole number of days'),
            'unknown clock': (arc(lambda a: a.update(clock='ghost')),
                              'arcs.json: arc missing_lens names clock "ghost", which is not in clocks.json'),
            'missing key': (arc(lambda a: a.pop('steps')), 'arcs.json: arcs[0] needs "steps"'),
        }
        for label, (change, message) in cases.items():
            with self.subTest(label):
                pack = self.fresh()
                self.edit(pack, 'arcs.json', change)
                self.assertTrue(any(e.startswith(message) for e in check_pack(pack).errors), check_pack(pack).errors)
                with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
                    cli.campaign_new(argparse.Namespace(novel=self.slug, start='start'))
        pack = self.fresh()
        (pack / 'arcs.json').write_text('{}', encoding='utf-8')
        self.assertTrue(any('needs "version": 1 and "arcs"' in e for e in check_pack(pack).errors))

    def test_a_duplicate_clock_id_in_one_file_is_an_error(self):
        pack = self.fresh()
        self.edit(pack, 'clocks.json', lambda clocks: clocks.append(dict(clocks[0])))
        errors = [e for e in check_pack(pack).errors if e.startswith('clocks.json')]
        self.assertEqual(len(errors), 1, errors)
        self.assertIn('duplicate clock id', errors[0])
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            cli.campaign_new(argparse.Namespace(novel=self.slug, start='start'))

    def test_installing_the_same_clocks_again_still_skips_the_existing_ones(self):
        pack = self.fresh()
        clocks = json.loads((pack / 'clocks.json').read_text(encoding='utf-8'))
        with closing(sqlite3.connect(':memory:')) as con:
            con.row_factory = sqlite3.Row
            planner = world_setup._DryPlanner(con, world_setup.load(pack)[0]['nodes'])
            self.assertEqual(planner.install_chronicle(clocks, 'chronology.json'), len(clocks))
            self.assertEqual(planner.install_chronicle(clocks, 'chronology.json'), 0)


if __name__ == '__main__':
    unittest.main()
