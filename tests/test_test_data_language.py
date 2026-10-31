"""The data and names inside the tests are English, except in the few tests that exercise French on purpose."""
import ast
from pathlib import Path
import re
import unittest

TESTS = Path(__file__).resolve().parent
# Tests whose French text is the point: the French packs, the migration wording list and the accent-folding checks.
FRENCH_ON_PURPOSE = {'test_prompts.py', 'test_pack.py', 'test_ui_text.py', 'test_migration_wording.py',
                     'test_no_private_traces.py', 'test_test_data_language.py'}
FRENCH = {'visite', 'bois', 'sain', 'pont', 'pisteuse', 'consigne', 'merci', 'attendre', 'attaque', 'avertissement',
          'cercle', 'emeraude', 'guilde', 'panne', 'locale', 'ancien', 'ancienne', 'effet', 'nouvelle', 'contexte',
          'relire', 'autre', 'signe', 'texte', 'parle', 'quels', 'messagers', 'promesse', 'suivi', 'pouvoir', 'lieu',
          'histoire', 'encore', 'boum', 'sous', 'attente', 'ambassadeur', 'demande', 'rien', 'quitter', 'corps', 'casse'}
BRITISH = {'travelling'}


def words(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for word in re.findall(r'[A-Za-z]+', node.value):
                yield node.lineno, word
        elif isinstance(node, ast.Name):
            yield node.lineno, node.id
        elif isinstance(node, ast.arg):
            yield node.lineno, node.arg


class TestDataLanguage(unittest.TestCase):
    def test_test_data_has_no_french_word_left(self):
        found = []
        for path in sorted(TESTS.glob('*.py')):
            if path.name in FRENCH_ON_PURPOSE:
                continue
            for line, word in words(ast.parse(path.read_text(encoding='utf-8'))):
                if word.lower() in FRENCH | BRITISH:
                    found.append(f'{path.name}:{line}: {word}')
        self.assertEqual(found, [])


if __name__ == '__main__':
    unittest.main()
