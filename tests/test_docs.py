"""The documentation stays true: links resolve, commands exist, keys and files it names are real."""
import argparse
import re
import unittest
from pathlib import Path

from novel_engine import cli, prompts, ui_text
from novel_engine.pack import COLOR_KEYS, PRESETS

DOCS = Path(__file__).resolve().parents[1] / 'docs'
PAGES = sorted(DOCS.glob('*.md'))
LINK = re.compile(r'\]\(([^)#\s]+)(?:#[^)]*)?\)')
COMMAND = re.compile(r'`novel ([a-z]+)')


def text(page):
    return page.read_text(encoding='utf-8')


class DocsTest(unittest.TestCase):
    def test_there_are_about_ten_pages_and_an_index_that_lists_them_all(self):
        names = {p.name for p in PAGES}
        self.assertGreaterEqual(len(names), 10)
        index = text(DOCS / 'index.md')
        for name in names - {'index.md'}:
            self.assertIn(f'({name})', index, name)

    def test_every_relative_link_points_to_a_page(self):
        for page in PAGES:
            for target in LINK.findall(text(page)):
                if target.startswith(('http://', 'https://', 'mailto:')):
                    continue
                self.assertTrue((page.parent / target).is_file(), f'{page.name} links to {target}')

    def test_every_novel_command_named_exists(self):
        commands = set(cli.SCRIPTS) | {'doctor', 'init', 'ingest', 'canon', 'character', 'campaign', 'example', 'validate',
                                         'unblock', 'configure'}
        for page in PAGES:
            for name in COMMAND.findall(text(page)):
                self.assertIn(name, commands, f'{page.name}: novel {name}')
        for name in commands:
            self.assertIn(f'`novel {name}', text(DOCS / 'cli.md'), name)

    def test_the_keys_of_the_packs_are_documented(self):
        packs = text(DOCS / 'novel-packs.md')
        for preset in PRESETS:
            self.assertIn(f'`{preset}`', packs)
        for color in COLOR_KEYS:
            self.assertIn(f'`{color}`', packs)

    def test_named_files_exist(self):
        root = DOCS.parent / 'novel_engine'
        for relative in ('ui_text.py', 'resources/icons', 'resources/CREDITS.md', 'assets/examples/lantern_bay/novel.json',
                         'prompts/en/instructions.opening.md', 'prompts/en/agenda_protocol.instructions.md'):
            self.assertTrue((root / relative).exists(), relative)
        self.assertEqual(len(prompts.names('en')), 30)
        self.assertIn('(30 files)', text(DOCS / 'prompts-and-languages.md'))

    def test_no_page_mentions_the_original_project(self):
        for page in PAGES:
            for phrase in ('source project', 'hunger', 'older save'):
                self.assertNotIn(phrase, text(page), page.name)

    def test_every_option_of_novel_play_is_documented(self):
        from tests.test_cli_help import parser_of
        from novel_engine.scripts import play_narrative
        page = text(DOCS / 'cli.md')
        for action in parser_of(play_narrative.main)._actions:
            if action.option_strings and action.dest != 'help' and action.help != argparse.SUPPRESS:
                self.assertIn('`' + action.option_strings[0], page)

    def test_the_reader_page_opens_with_novel_read(self):
        first = text(DOCS / 'reader.md').split('\n\n')[1]
        self.assertTrue(first.startswith('`novel read`'), first)

    def test_no_page_uses_a_word_for_word_phrase(self):
        for page in PAGES:
            for phrase in ('consultation', '`French`-style'):
                self.assertNotIn(phrase, text(page), page.name)

    def test_the_documented_defaults_match_the_code(self):
        self.assertTrue(110 <= len(ui_text.EN) <= 130, 'docs say about 120 page texts')
        self.assertIn('about 120 keys', text(DOCS / 'prompts-and-languages.md'))
        from novel_engine.narration_codex import DEFAULT_NARRATIVE_TIMEOUT
        self.assertEqual(DEFAULT_NARRATIVE_TIMEOUT, 240)
        self.assertIn('240 seconds', text(DOCS / 'engines.md'))
        self.assertIn('12000', text(DOCS / 'canon.md'))


if __name__ == '__main__':
    unittest.main()
