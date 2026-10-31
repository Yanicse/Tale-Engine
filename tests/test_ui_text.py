"""Reader texts: complete language packs, and a page that only asks for keys that exist."""
import re
import unittest
from pathlib import Path

from novel_engine import ui_text
from novel_engine.pack import clean_theme

PAGE = (Path(__file__).resolve().parents[1] / 'novel_engine' / 'reader_page.html').read_text(encoding='utf-8')


class LanguagePacks(unittest.TestCase):
    def test_every_pack_has_exactly_the_english_keys(self):
        for code, pack in ui_text.PACKS.items():
            self.assertEqual(set(pack), set(ui_text.EN), code)

    def test_translations_keep_the_placeholders(self):
        for code, pack in ui_text.PACKS.items():
            for key, text in pack.items():
                self.assertEqual(set(ui_text.PLACEHOLDER.findall(text)),
                                 set(ui_text.PLACEHOLDER.findall(ui_text.EN[key])), (code, key))

    def test_the_french_pack_translates_every_text(self):
        same = {'tab.possessions', 'tab.agenda', 'mode.auto', 'mode.arcane', 'input.aria', 'mana.title', 'header.version'}
        untranslated = {key for key, text in ui_text.PACKS['fr'].items() if text == ui_text.EN[key]}
        self.assertEqual(sorted(untranslated - same), [])

    def test_language_names_and_codes(self):
        for value, expected in (('fr', 'fr'), ('fr-FR', 'fr'), ('French', 'fr'), ('Français', 'fr'), ('en', 'en'),
                                ('xx', 'en'), (None, 'en'), ('', 'en')):
            self.assertEqual(ui_text.language_code(value), expected, value)

    def test_novel_labels_override_the_language_pack(self):
        labels = ui_text.labels_for('fr', {'send': 'Go', 'nope': 'x', 'tab.npc': ''})
        self.assertEqual((labels['send'], labels['tab.npc'], 'nope' in labels), ('Go', 'PNJ', False))
        self.assertEqual(ui_text.labels_for('xx')['send'], 'Send')

    def test_theme_accepts_any_known_key_and_drops_the_rest(self):
        theme = clean_theme({'labels': {'search.story': 'Find', 'unknown.key': 'x'}})
        self.assertEqual(theme['labels'], {'search.story': 'Find'})


class PageUsesKnownKeys(unittest.TestCase):
    def test_every_key_asked_by_the_script_exists(self):
        # Keys built at run time ('mode.' + name, 'tab.' + id) are checked through the lists below.
        asked = {k for k in re.findall(r"\btr\('([a-z_.]+)'", PAGE) if not k.endswith('.')}
        asked |= {f'mode.{m}' for m in ('auto', 'day', 'night', 'arcane', 'frost', 'scifi')}
        asked |= {f'precision.{p}' for p in ('exact', 'approximate', 'unknown')}
        asked |= {f'tab.{t}' for t in ('story', 'scene', 'npc', 'possessions', 'agenda', 'summaries', 'abilities', 'creations')}
        self.assertTrue(asked)
        self.assertEqual(sorted(asked - set(ui_text.EN)), [])

    def test_every_key_in_the_markup_exists(self):
        keys = set(re.findall(r'data-t(?:-aria|-title|-placeholder)?="([a-z_.]+)"', PAGE))
        self.assertTrue(keys)
        self.assertEqual(sorted(keys - set(ui_text.EN)), [])

    def test_no_english_prose_is_left_in_the_script(self):
        script = PAGE[PAGE.index('<script>'):PAGE.index('</script>')]
        for phrase in ('No scene', 'Reading failed', 'Turn recorded', 'Search the story', 'Hit points', 'Sending...',
                       'not counted', 'place unknown'):
            self.assertNotIn("'" + phrase, script)

    def test_header_chips_are_translated(self):
        script = PAGE[PAGE.index('<script>'):PAGE.index('</script>')]
        for phrase in ("' exchanges'", "'version '", 'en waiting'):
            self.assertNotIn(phrase, script)
        for key in ('header.exchanges', 'header.version', 'header.pending'):
            self.assertIn("tr('" + key + "'", script)
        self.assertEqual(ui_text.labels_for('fr')['header.pending'], 'action en attente')

    def test_the_page_code_uses_english_names(self):
        # Class and function names of the page, outside the translated texts.
        for name in ('titres', 'paragraphes', 'classer', 'classe', 'or', 'sou', 'lib', 'som'):
            self.assertIsNone(re.search(r"(class=\"|[.'(,]|function )" + name + r"\b(?![.:])", PAGE), name)


if __name__ == '__main__':
    unittest.main()
