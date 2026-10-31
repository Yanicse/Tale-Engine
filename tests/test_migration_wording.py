"""Wording left by the migration: French words, calques and traces of the original campaign, with no model call."""
import ast
import hashlib
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from novel_engine import instructions
from novel_engine.indexed_memory import TOPICS

PACKAGE = ROOT / 'novel_engine'
ASSETS = PACKAGE / 'assets'
# Phrases that must not come back in the code strings, the prompts or the agent rules.
FORBIDDEN = ('Ponctuation', 'Ressource ', 'Lien narrative', 'en double', 'Version narrative', 'Budget UTF-8 positive',
             'Base existing', 'Plan JSON prospective', 'Service Codex', 'persistent file', 'file identifier',
             'outside this file', 'the prototype', 'in the chat', 'RULE 20261017', 'cooldown an effect',
             '/commitments/accord', 'enlisted', 'taken by force', 'Codex loop', 'The Codex narrator', 'memory files',
             'closes the file', 'whole file', 'revelation of a private file', 'at the Guild', 'finished house', "'chef'", 'plaque',
             "'renamed'", '; An ', 'accessible files', 'NPC ignores', 'buying the balance', 'canonical search scripts')


def code_strings():
    for path in sorted(PACKAGE.rglob('*.py')):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                yield path.relative_to(ROOT), node.lineno, node.value


def documents():
    for path in sorted((PACKAGE / 'prompts').rglob('*.md')) + sorted((ASSETS / 'docs').rglob('*.md')):
        yield path.relative_to(ROOT), path.read_text(encoding='utf-8')


class MigrationWordingTests(unittest.TestCase):
    def test_code_strings_have_no_migration_leftover(self):
        found = [f'{path}:{line}: {phrase}' for path, line, value in code_strings()
                 for phrase in FORBIDDEN if phrase in value]
        self.assertEqual(found, [])

    def test_code_word_lists_hold_no_french_word_or_duplicate(self):
        source = (PACKAGE / 'canon_selection.py').read_text(encoding='utf-8') + (PACKAGE / 'planner.py').read_text(encoding='utf-8')
        found = [phrase for phrase in FORBIDDEN if phrase in source]
        found += re.findall(r"('(\w+)',\s*'\2')", source)
        self.assertEqual(found, [])

    def test_prompts_and_agent_rules_have_no_migration_leftover(self):
        found = [f'{path}: {phrase}' for path, text in documents() for phrase in FORBIDDEN if phrase in text]
        self.assertEqual(found, [])

    def test_default_memory_topics_fit_any_novel(self):
        self.assertEqual([topic[0] for topic in TOPICS], ['magic'])

    def test_known_paragraph_signatures_match_the_shipped_documents(self):
        for registry in (instructions.KNOWN_DOCUMENT_PARAGRAPHS, instructions.DOCUMENT_DUPLICATES):
            for source, signatures in registry.items():
                text = (ASSETS / source).read_text(encoding='utf-8')
                current = {hashlib.sha256(p.encode('utf-8')).hexdigest() for p in re.split(r'\n\s*\n', text)}
                with self.subTest(source=source):
                    self.assertEqual(set(signatures) - current, set())

    def test_interactive_help_lists_every_handled_command(self):
        source = (PACKAGE / 'scripts' / 'play_narrative.py').read_text(encoding='utf-8')
        help_line = next(line for line in source.splitlines() if 'Commands: /quit' in line)
        for command in set(re.findall(r"'(/[a-z]+)'", source)):
            with self.subTest(command=command):
                self.assertIn(command, help_line)


if __name__ == '__main__':
    unittest.main()
