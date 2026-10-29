"""Prompt texts live in files: none missing, none orphaned, translatable folder by folder."""
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from novel_engine import prompts

SOURCE = Path(prompts.__file__).resolve().parent
USED = re.compile(r"load_prompt\('([a-z_]+\.[a-z_]+)'\)")


def used_in_code():
    found = set()
    for path in SOURCE.glob('*.py'):
        found |= set(USED.findall(path.read_text(encoding='utf-8')))
    return found


class PromptFiles(unittest.TestCase):
    def test_every_prompt_used_by_the_code_has_a_file_and_every_file_is_used(self):
        files = set(prompts.names('en'))
        used = used_in_code()
        self.assertEqual(sorted(used - files), [], 'prompt asked by the code but missing')
        self.assertEqual(sorted(files - used), [], 'prompt file that no code loads')
        self.assertGreaterEqual(len(files), 30)

    def test_prompts_are_not_empty_and_have_no_em_dash(self):
        for name in prompts.names('en'):
            text = prompts.load(name)
            self.assertTrue(text.strip(), name)
            self.assertNotIn(chr(0x2014), text, name)

    def test_placeholders_that_the_code_fills_exist(self):
        story = prompts.load('instructions.story')
        self.assertEqual(sorted(set(re.findall(r'(?<!\{)\{([a-z_]+)\}(?!\})', story))), ['word_target'])
        extractor = prompts.load('canon_build.prompt')
        self.assertEqual(sorted(set(re.findall(r'(?<!\{)\{([a-z_]+)\}(?!\})', extractor))), ['excerpt', 'first', 'last', 'limit'])

    def test_modules_expose_the_loaded_text(self):
        from novel_engine import instructions
        self.assertEqual(instructions.OPENING, prompts.load('instructions.opening'))
        self.assertTrue(instructions.OPENING.startswith('You are only the narrator'))

    def test_unknown_prompt_is_an_error(self):
        with self.assertRaises(FileNotFoundError):
            prompts.load('instructions.nothing')


class Translation(unittest.TestCase):
    def test_a_language_folder_overrides_some_texts_and_falls_back_to_english_for_the_rest(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / 'en').mkdir()
        (tmp / 'fr').mkdir()
        (tmp / 'en' / 'a.one.md').write_text('English one', encoding='utf-8')
        (tmp / 'en' / 'a.two.md').write_text('English two', encoding='utf-8')
        (tmp / 'fr' / 'a.one.md').write_text('Un en francais', encoding='utf-8')
        prompts.load.cache_clear()
        self.addCleanup(prompts.load.cache_clear)
        with mock.patch.object(prompts, 'FOLDER', tmp):
            self.assertEqual(prompts.languages(), ['en', 'fr'])
            self.assertEqual(prompts.load('a.one', 'fr'), 'Un en francais')
            self.assertEqual(prompts.load('a.two', 'fr'), 'English two')
            self.assertEqual(prompts.load('a.one'), 'English one')
            self.assertEqual(prompts.load('a.one', 'xx'), 'English one')


class EnvelopeVersion(unittest.TestCase):
    def test_instructions_announce_the_version_the_schema_requires(self):
        from novel_engine.narrative_preparation import ALL_MODULES, build_instructions
        plain = build_instructions({'campaign_rules': {}}, list(ALL_MODULES), 180)
        self.assertIn('envelope is version 1.2', plain)
        mechanics = build_instructions({'campaign_rules': {'mechanics': {'version': 'mana-1.0'}}}, list(ALL_MODULES), 180)
        self.assertIn('envelope is version 1.3', mechanics)
        self.assertNotIn('version 1.2', mechanics)


if __name__ == '__main__':
    unittest.main()
