"""What a campaign created from a pack really has: creations, summaries, chronicler and arcs when the pack asks."""
from contextlib import closing, redirect_stderr, redirect_stdout
import argparse
import io
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from novel_engine import cli, resume, world_setup
from novel_engine.arcs import ArcRegistry
from novel_engine.contracts import ROOT
from novel_engine.pack_check import check_pack
from novel_engine.planner import WorldPlanner


class Fixture(unittest.TestCase):
    slug = 'newcampaigntest'

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='.tmp-newcampaign-', dir=ROOT))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.target = ROOT / 'campaigns' / self.slug
        self.addCleanup(shutil.rmtree, self.target, True)
        self.active = resume.ACTIVE
        self.before = self.active.read_text(encoding='utf-8') if self.active.is_file() else None
        self.addCleanup(self.restore_active)
        patch = mock.patch.object(cli, 'NOVELS', self.tmp)
        patch.start()
        self.addCleanup(patch.stop)

    def restore_active(self):
        if self.before is None:
            self.active.unlink(missing_ok=True)
        else:
            self.active.write_text(self.before, encoding='utf-8')

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


if __name__ == '__main__':
    unittest.main()
