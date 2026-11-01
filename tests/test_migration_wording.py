"""Wording left by the migration: French words, calques and traces of the original campaign, with no model call."""
import ast
import hashlib
import json
from pathlib import Path
import re
import sys
import tokenize
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from novel_engine import instructions
from novel_engine.canon_selection import GENERAL_PREDICATES
from novel_engine.indexed_memory import TOPICS

PACKAGE = ROOT / 'novel_engine'
ASSETS = PACKAGE / 'assets'
# Phrases that must not come back in the code strings, the prompts or the agent rules.
FORBIDDEN = ('Ponctuation', 'Ressource ', 'Lien narrative', 'en double', 'Version narrative', 'Budget UTF-8 positive',
             'Base existing', 'Plan JSON prospective', 'Service Codex', 'persistent file', 'file identifier',
             'outside this file', 'the prototype', 'in the chat', 'RULE 20261017', 'cooldown an effect',
             '/commitments/accord', 'enlisted', 'taken by force', 'Codex loop', 'The Codex narrator', 'memory files',
             'closes the file', 'whole file', 'revelation of a private file', 'at the Guild', 'finished house', "'chef'", 'plaque',
             "'renamed'", '; An ', 'accessible files', 'NPC ignores', 'buying the balance', 'canonical search scripts', 'play_narrative.py --',
             'consultation', 'official path', 'impose itself', 'The manager', 'Ordinary arbitrations', 'historical demo',
             'nor make calls', 'personal knowledge of the work', 'An attested knowledge',
             'allows deciding', 'a certain ability', 'nor the whole', 'stop the useful work', 'the manager',
             'decision taken now apart', 'Proposition contract', 'nor declaring', 'display as acquired', 'during reading',
             'an arbitration that is added', 'with no attribution to the canon', 'of the player not defined',
             'incertitude', 'theocratie', 'U+2014', 'ncorrect integrity', 'ncorrect SQLite integrity',
             'Business compatibility', 'be a text', 'be a non-empty text', 'needs a text', 'is not a text of the page',
             'a really open', 'a past nor contradicting', 'gauge nor', 'systematically nor', 'event nor reveal',
             'date, nor finishing', 'fact nor invent', 'audience nor expose', 'preparations nor making',
             'success nor arbitrary', 'replay it nor make', 'character nor to an NPC', 'past nor a supposition',
             'balances nor an assumed', 'arrival nor grant', 'testimony or an offer stay ', 'postponement provide ',
             'automatically give no knowledge', 'a lot of elapsed time', 'Descriptive_marker',
             'resume its identifier', 'Deletion of a missing value', 'fact nor ask', 'No absence means', 'Non-lethal blow or exceeding', 'documentary exclusion', 'An engaged arc', 'reserved to a rejected', 'Positions attest', 'finding creates no news', 'treat it as accomplished', 'Burst clocks whose',
             'default dosage', 'to the dosage', 'with the dosage',
             'Acquired step', 'accomplished off-screen', 'accomplished project', 'accomplished developments', 'accomplished consequence', 'already accomplished', 'acquired by the world', 'as acquired', 'what is acquired', 'into acquired', 'precisely accomplishes', 'without accomplishing', 'when a step is accomplished', 'no proof', 'campaign proof', 'precise proof', 'neither proof', 'of its proof', 'a proof is', 'new proof', 'main proofs', 'local proofs', 'Keeping proofs', 'exact proofs kept', 'the proofs, not', 'designating', 'Never decide their actions, nor',
             'acquired fact', 'acquired steps', 'accomplished step', 'really accomplished', 'a proof of', 'designate', 'proof of knowledge', 'proofs of knowledge', 'invent neither', 'acquired causes', 'keep the unknown.', 'historical proofs', 'building site', 'designates a', 'numbered withdrawal', 'movement is accomplished', 'never acquired', 'step is accomplished', 'the acquired', 'content of the proofs', 'automatic acquired', 'stay acquired', 'really accomplished', 'the proof of', 'acquired result', 'Once the action is accomplished', 'accomplished steps', 'creates no acquired', 'same proofs')

# Phrases that must not come back in the comments of the code.
FORBIDDEN_COMMENTS = ('restored big event', 'designate a proof', 'its proof', 'An accomplished step', 'neither proof',
                       'main proofs', 'the proofs, not', 'a proof is', 'local proofs', 'Keeping proofs')


def code_strings():
    for path in sorted(PACKAGE.rglob('*.py')):
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                yield path.relative_to(ROOT), node.lineno, node.value


def code_comments():
    for path in sorted(PACKAGE.rglob('*.py')):
        with path.open(encoding='utf-8') as handle:
            for token in tokenize.generate_tokens(handle.readline):
                if token.type == tokenize.COMMENT:
                    yield path.relative_to(ROOT), token.start[0], token.string


def documents():
    for path in sorted((PACKAGE / 'prompts').rglob('*.md')) + sorted((ASSETS / 'docs').rglob('*.md')):
        yield path.relative_to(ROOT), path.read_text(encoding='utf-8')


def json_strings():
    for path in sorted(ASSETS.rglob('*.json')):
        values = [json.loads(path.read_text(encoding='utf-8'))]
        while values:
            value = values.pop()
            if isinstance(value, dict):
                values += list(value) + list(value.values())
            elif isinstance(value, list):
                values += value
            elif isinstance(value, str):
                yield path.relative_to(ROOT), value


class MigrationWordingTests(unittest.TestCase):
    def test_code_strings_have_no_migration_leftover(self):
        found = [f'{path}:{line}: {phrase}' for path, line, value in code_strings()
                 for phrase in FORBIDDEN if phrase in value]
        self.assertEqual(found, [])

    def test_code_comments_have_no_migration_leftover(self):
        found = [f'{path}:{line}: {phrase}' for path, line, value in code_comments()
                 for phrase in FORBIDDEN_COMMENTS if phrase in value]
        self.assertEqual(found, [])

    def test_no_literal_list_of_strings_repeats_an_item(self):
        found = []
        for path in sorted(PACKAGE.rglob('*.py')):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if isinstance(node, (ast.Tuple, ast.List)) and node.elts and all(
                        isinstance(item, ast.Constant) and isinstance(item.value, str) for item in node.elts):
                    values = [item.value for item in node.elts]
                    found += [f'{path.relative_to(ROOT)}:{node.lineno}: {value}' for value in set(values)
                              if values.count(value) > 1]
        self.assertEqual(found, [])

    def test_code_word_lists_hold_no_french_word_or_duplicate(self):
        source = (PACKAGE / 'canon_selection.py').read_text(encoding='utf-8') + (PACKAGE / 'planner.py').read_text(encoding='utf-8')
        found = [phrase for phrase in FORBIDDEN if phrase in source]
        found += re.findall(r"('(\w+)',\s*'\2')", source)
        found += re.findall(r'\((?:\w+\|)*(\w+)\|(?:\w+\|)*\1(?:\|\w+)*\)', source)
        self.assertEqual(found, [])

    def test_prompts_and_agent_rules_have_no_migration_leftover(self):
        found = [f'{path}: {phrase}' for path, text in documents() for phrase in FORBIDDEN if phrase in text]
        self.assertEqual(found, [])

    def test_json_resources_have_no_migration_leftover(self):
        found = sorted({f'{path}: {phrase}' for path, value in json_strings() for phrase in FORBIDDEN if phrase in value})
        self.assertEqual(found, [])

    def test_keys_are_english(self):
        french = {'bloc', 'planification'}
        found = [f'{path}:{line}: {value}' for path, line, value in code_strings() if value in french]
        settings = json.loads((PACKAGE / 'targeted_context_settings.json').read_text(encoding='utf-8'))
        found += sorted(set(settings['reductions']) & french)
        self.assertEqual(found, [])

    def test_default_memory_topics_fit_any_novel(self):
        self.assertEqual([topic[0] for topic in TOPICS], ['magic'])

    def test_general_canon_predicates_fit_any_novel(self):
        self.assertEqual(sorted(GENERAL_PREDICATES), ['creator', 'geography', 'nature', 'rank_hierarchy', 'species'])
        source = (PACKAGE / 'canon_selection.py').read_text(encoding='utf-8')
        self.assertNotIn("'sheet_", source)

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
