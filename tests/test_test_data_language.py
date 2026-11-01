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

# Words and phrases that tests names and docstrings no longer use: the code says evidence, established and learned.
OLD_NAMING = {'dossier', 'lecture', 'proof', 'proofs', 'acquired'}
OLD_PHRASES = ('independent from', 'complete journey', 'guild file')


def declared_text(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Module)):
            doc = ast.get_docstring(node)
            if doc:
                yield getattr(node, 'lineno', 1), doc
            if not isinstance(node, ast.Module):
                yield node.lineno, re.sub(r'(?<=[a-z])(?=[A-Z])|_', ' ', node.name)


class TestDataLanguage(unittest.TestCase):
    def test_test_names_and_docstrings_use_the_wording_of_the_code(self):
        found = []
        for path in sorted(TESTS.glob('*.py')):
            if path.name in FRENCH_ON_PURPOSE:
                continue
            for line, text in declared_text(ast.parse(path.read_text(encoding='utf-8'))):
                lowered = text.lower()
                for word in OLD_NAMING & set(re.findall(r'[a-z]+', lowered)):
                    found.append(f'{path.name}:{line}: {word}')
                found.extend(f'{path.name}:{line}: {phrase}' for phrase in OLD_PHRASES if phrase in lowered)
        self.assertEqual(found, [])

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
