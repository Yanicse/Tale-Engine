"""Sentences the program writes agree in number: `1 batch`, not `1 batches`."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from novel_engine import ui_text
from novel_engine.diagnostic_cli import failure_summary
from novel_engine.wording import counted


class CountedTests(unittest.TestCase):
    def test_the_noun_agrees_with_the_number(self):
        self.assertEqual(counted(0, 'page'), '0 pages')
        self.assertEqual(counted(1, 'page'), '1 page')
        self.assertEqual(counted(2, 'batch', 'batches'), '2 batches')
        self.assertEqual(counted(1, 'batch', 'batches'), '1 batch')

    def test_a_timeout_names_one_second_in_the_singular(self):
        error = {'category': 'timeout', 'timeout_seconds': 1, 'detail': ''}
        self.assertIn('Limit: 1 second.', failure_summary(error))
        error['timeout_seconds'] = 90
        self.assertIn('Limit: 90 seconds.', failure_summary(error))

    def test_page_texts_with_a_number_need_no_plural_marker(self):
        for pack in ui_text.PACKS.values():
            for key in ('header.exchanges', 'search.results', 'npc.count'):
                self.assertNotIn('(s)', pack[key])
                self.assertTrue(pack[key].index('{n}') > 0, key)


if __name__ == '__main__':
    unittest.main()
