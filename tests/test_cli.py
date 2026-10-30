from contextlib import closing
import io
import json
import sqlite3
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from novel_engine import canon_build, cli, ingest
from novel_engine.contracts import ROOT
from novel_engine.memory import CanonRepository

QUOTE = 'Mira sails from the harbor at dawn'
RESPONSE = {'entities': [{'id': 'mira', 'kind': 'character', 'name': 'Mira', 'aliases': ['the fox']},
                         {'id': 'harbor', 'kind': 'place', 'name': 'Harbor'}],
            'claims': [{'subject': 'mira', 'statement': 'Mira sails from the harbor at dawn.', 'predicate': 'travel',
                        'quote': QUOTE, 'page': 1},
                       {'subject': 'mira', 'statement': 'Mira sails from the harbor at dawn.', 'predicate': 'travel',
                        'quote': QUOTE, 'page': 1},
                       {'subject': 'ghost', 'statement': 'Unknown subject is dropped.', 'predicate': 'x',
                        'quote': QUOTE, 'page': 1},
                       {'subject': 'mira', 'statement': 'Mira owns a castle.', 'predicate': 'owns',
                        'quote': 'Mira owns a castle', 'page': 1}]}


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='.tmp-cli-', dir=ROOT))
        patch = mock.patch.object(cli, 'NOVELS', self.tmp)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_cli(self, *argv):
        return cli.main(list(argv))

    def make_book(self):
        book = self.tmp / 'book.txt'
        book.write_text('Mira sails from the harbor at dawn.\n\nThe sea is calm.', encoding='utf-8')
        return book

    def test_doctor_passes_on_supported_python(self):
        self.assertEqual(self.run_cli('doctor'), 0)

    def test_init_ingest_and_manual_canon_round_trip(self):
        self.run_cli('init', 'demo', '--title', 'Demo')
        self.run_cli('ingest', str(self.make_book()), '--novel', 'demo')
        pack = self.tmp / 'demo'
        self.assertEqual(len(list(canon_build.read_pages(pack))), 1)
        # Manual engine without an answer: prompts written, nothing built.
        result = canon_build.build(pack, 'manual', log=lambda *_: None)
        self.assertEqual(len(result['awaiting_manual_answer']), 1)
        batch = canon_build.plan(pack)[0]
        self.assertIn('Mira sails', (pack / 'work' / (batch['id'] + '.prompt.md')).read_text(encoding='utf-8'))
        (pack / 'work' / (batch['id'] + '.response.json')).write_text('```json\n' + json.dumps(RESPONSE) + '\n```', encoding='utf-8')
        result = canon_build.build(pack, 'manual', log=lambda *_: None)
        self.assertEqual((result['remaining'], result['failed'], result['claims']), (0, [], 1))
        repo = CanonRepository(pack / 'canon.sqlite', 1)
        self.assertTrue(repo.search('harbor dawn'))

    def test_output_is_utf8_when_the_console_code_page_cannot_encode_it(self):
        # A redirected output on Windows uses the ANSI code page: printing Cyrillic must not fail.
        self.run_cli('init', 'demo')
        pack = self.tmp / 'demo'
        (pack / 'text').mkdir(exist_ok=True)
        page = 'Мира плывёт из гавани.'
        (pack / 'text' / 'v01.jsonl').write_text(json.dumps({'volume': 1, 'page': 1, 'text': page}) + '\n',
                                                encoding='utf-8')
        batch = canon_build.plan(pack)[0]
        (pack / 'work').mkdir(exist_ok=True)
        answer = {'entities': [{'id': 'mira', 'kind': 'character', 'name': 'Мира'}],
                  'claims': [{'subject': 'mira', 'statement': page, 'predicate': 'travel', 'quote': page[:-1], 'page': 1}]}
        (pack / 'work' / (batch['id'] + '.response.json')).write_text(json.dumps(answer), encoding='utf-8')
        canon_build.build(pack, 'manual', log=lambda *_: None)
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding='cp1252')
        with mock.patch.object(sys, 'stdout', stream):
            self.assertEqual(self.run_cli('canon', 'search', 'demo', 'гавани'), 0)
            stream.flush()
        self.assertIn(page, raw.getvalue().decode('utf-8'))

    def test_game_commands_forward_leading_options(self):
        seen = {}

        def fake(module, run_name=None, alter_sys=False):
            seen['argv'] = list(sys.argv)
        with mock.patch.object(cli.runpy, 'run_module', fake):
            self.assertEqual(cli.main(['play', '--text', 'I wait.', '--engine', 'manual']), 0)
        self.assertEqual(seen['argv'][1:], ['--text', 'I wait.', '--engine', 'manual'])

    def test_the_help_of_a_game_command_names_the_novel_command(self):
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding='utf-8')
        with mock.patch.object(sys, 'stdout', stream):
            self.assertEqual(cli.main(['resume', '--help']), 0)
            stream.flush()
        self.assertTrue(raw.getvalue().startswith(b'usage: novel resume'), raw.getvalue()[:60])

    def test_the_ability_prompt_shows_a_single_percent_sign(self):
        with mock.patch('builtins.input', side_effect=['']) as asked:
            cli.ability_rules([], ask_abilities=True)
        prompt = asked.call_args[0][0]
        self.assertIn('% of max mana', prompt)
        self.assertNotIn('%%', prompt)

    def test_remote_engine_requires_confirmation(self):
        self.run_cli('init', 'demo')
        self.run_cli('ingest', str(self.make_book()), '--novel', 'demo')
        with mock.patch.object(canon_build, 'build') as build:
            self.run_cli('canon', 'build', 'demo', '--engine', 'claude')
            build.assert_not_called()

    def test_invalid_answer_is_reported_not_stored(self):
        self.run_cli('init', 'demo')
        self.run_cli('ingest', str(self.make_book()), '--novel', 'demo')
        pack = self.tmp / 'demo'
        batch = canon_build.plan(pack)[0]
        (pack / 'work' / (batch['id'] + '.response.json')).write_text('not json', encoding='utf-8')
        result = canon_build.build(pack, 'manual', log=lambda *_: None)
        self.assertEqual((result['failed'], result['remaining']), ([batch['id']], 1))

    def test_epub_ingest(self):
        epub = self.tmp / 'b.epub'
        with zipfile.ZipFile(epub, 'w') as z:
            z.writestr('META-INF/container.xml', '<container><rootfiles><rootfile full-path="OEBPS/c.opf"/></rootfiles></container>')
            z.writestr('OEBPS/c.opf', '<package><manifest><item id="a" href="a.xhtml"/></manifest><spine><itemref idref="a"/></spine></package>')
            z.writestr('OEBPS/a.xhtml', '<html><body><p>First <b>chapter</b>.</p><script>x</script></body></html>')
        self.assertEqual(ingest.extract_pages(epub), ['First chapter.'])

    def test_campaign_new_creates_and_selects_a_save(self):
        from novel_engine import resume
        self.run_cli('init', 'clitest')
        self.run_cli('ingest', str(self.make_book()), '--novel', 'clitest')
        pack = self.tmp / 'clitest'
        batch = canon_build.plan(pack)[0]
        (pack / 'work' / (batch['id'] + '.response.json')).write_text(json.dumps(RESPONSE), encoding='utf-8')
        canon_build.build(pack, 'manual', log=lambda *_: None)
        self.run_cli('character', 'new', 'clitest', '--name', 'Ana', '--hp', '50')
        active = resume.ACTIVE
        before = active.read_text(encoding='utf-8') if active.is_file() else None
        campaigns = ROOT / 'campaigns' / 'clitest'
        existed = campaigns.exists()
        try:
            self.run_cli('campaign', 'new', 'clitest')
            selected, campaign = resume.active_campaign()
            self.assertEqual(campaign.config()['player_name'], 'Ana')
        finally:
            if before is None:
                active.unlink(missing_ok=True)
            else:
                active.write_text(before, encoding='utf-8')
            if not existed:
                shutil.rmtree(campaigns, True)


if __name__ == '__main__':
    unittest.main()


class CanonBuildTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='.tmp-canon-', dir=ROOT))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / 'text').mkdir()

    def write_pages(self, pages, volume=1):
        lines = [json.dumps({'volume': volume, 'page': n, 'text': text}) for n, text in pages]
        (self.tmp / 'text' / f'v{volume:02d}.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf-8')

    def answer(self, batch, quote, subject='mira'):
        data = {'entities': [{'id': subject, 'kind': 'character', 'name': subject.title()}],
                'claims': [{'subject': subject, 'statement': 'A fact about ' + subject + '.', 'predicate': 'fact',
                            'quote': quote, 'page': batch['first']}]}
        (self.tmp / 'work').mkdir(exist_ok=True)
        (self.tmp / 'work' / (batch['id'] + '.response.json')).write_text(json.dumps(data), encoding='utf-8')

    def test_claim_without_a_matching_quote_is_dropped_and_page_comes_from_the_quote(self):
        self.write_pages([(1, 'Mira sails.'), (2, 'The harbor wakes at dawn.')])
        batch = canon_build.plan(self.tmp)[0]
        data = {'entities': [{'id': 'mira', 'name': 'Mira'}],
                'claims': [{'subject': 'mira', 'statement': 'The harbor wakes.', 'predicate': 'x',
                            'quote': 'the Harbor wakes - at DAWN', 'page': 1},
                           {'subject': 'mira', 'statement': 'Invented.', 'predicate': 'x',
                            'quote': 'Not in the text', 'page': 1}]}
        entities, claims = canon_build.validate(data, batch)
        self.assertEqual([(c[1], c[3]) for c in claims], [('The harbor wakes.', 2)])
        data['claims'] = data['claims'][1:]
        with self.assertRaises(ValueError):
            canon_build.validate(data, batch)

    def test_sources_keep_the_quote(self):
        self.write_pages([(1, 'Mira sails from the harbor at dawn.')])
        self.answer(canon_build.plan(self.tmp)[0], 'Mira sails from the harbor')
        canon_build.build(self.tmp, 'manual', log=lambda *_: None)
        with closing(sqlite3.connect(self.tmp / 'canon.sqlite')) as con:
            self.assertEqual(con.execute('SELECT pdf_page,quote FROM sources').fetchall(), [(1, 'Mira sails from the harbor')])

    def test_resume_after_a_budget_change_only_sends_the_pages_left(self):
        pages = [(n, f'Page {n} says that Mira walks. ' * 20) for n in (1, 2, 3, 4)]
        self.write_pages(pages)
        small = canon_build.plan(self.tmp, budget=200)
        self.assertEqual(len(small), 4)
        self.answer(small[0], 'Page 1 says that Mira walks')
        first = canon_build.build(self.tmp, 'manual', budget=200, limit=1, log=lambda *_: None)
        self.assertEqual((first['remaining'], first['volumes']), (3, {1: 'open'}))
        left = canon_build.pending_batches(self.tmp, budget=100000)
        self.assertEqual([(b['first'], b['last']) for b in left], [(2, 4)])
        self.answer(left[0], 'Page 3 says that Mira walks')
        last = canon_build.build(self.tmp, 'manual', budget=100000, log=lambda *_: None)
        self.assertEqual((last['remaining'], last['failed'], last['volumes']), (0, [], {1: 'closed'}))
        with closing(sqlite3.connect(self.tmp / 'canon.sqlite')) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM build_pages').fetchone()[0], 4)

    def test_an_older_build_without_page_tracking_is_recovered(self):
        self.write_pages([(1, 'One.'), (2, 'Two.'), (3, 'Three.')])
        with closing(canon_build.connect(self.tmp)) as con:
            con.execute('DELETE FROM build_pages')
            con.execute("INSERT INTO build_batches VALUES ('v01_p0001_0002','done')")
            con.commit()
        self.assertEqual(sorted(canon_build.done_pages(self.tmp)), [(1, 1), (1, 2)])
        self.assertEqual([(b['first'], b['last']) for b in canon_build.pending_batches(self.tmp)], [(3, 3)])

    def test_entities_merge_by_name_or_by_alias_of_the_same_kind(self):
        with closing(canon_build.connect(self.tmp)) as con:
            rows = [('mira', 'character', 'Mira', ['the Fox']), ('fox', 'character', 'The Fox', []),
                    ('mira_2', 'character', 'MIRA', []), ('fox_place', 'place', 'The Fox', [])]
            for ident, kind, name, aliases in rows:
                con.execute('INSERT INTO entities VALUES (?,?,?,?)', (ident, kind, name, 'named'))
                for alias in [name] + aliases:
                    con.execute('INSERT INTO aliases VALUES (?,?)', (ident, alias))
            groups = canon_build.entity_groups(con)
        self.assertEqual((groups['fox'], groups['mira_2']), ('mira', 'mira'))
        # Same name merges whatever the kind; the place shares the character's name 'The Fox'.
        self.assertEqual(groups['fox_place'], 'mira')
