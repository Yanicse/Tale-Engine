"""Short proofs complete the summaries, without changing the canon or the game."""
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from novel_engine.contracts import check_json
from novel_engine.instructions import DIALOGUE
import test_documentary_loops as fixtures

CLAIM = 'v01_p0001_0004:c1'


class SourceExcerptTests(unittest.TestCase):
    setUp = fixtures.DocumentaryFixture.setUp
    tearDown = fixtures.DocumentaryFixture.tearDown
    selector = fixtures.DocumentaryFixture.selector

    def quote(self, text):
        with closing(sqlite3.connect(self.snapshot)) as con, con:
            con.execute('ALTER TABLE sources ADD COLUMN quote TEXT')
            con.execute('UPDATE sources SET quote=? WHERE claim_id=?', (text, CLAIM))

    def digest(self):
        return hashlib.sha256(self.snapshot.read_bytes()).hexdigest()

    def test_short_evidence_keeps_information_omitted_from_statement(self):
        self.quote('The best-known team in the region was the Emerald Circle, composed of five adventurers.')
        before = self.digest()
        packet = self.selector().select(query='canon:' + CLAIM)
        fact = packet['facts'][0]
        self.assertNotIn('best-known', fact['statement'])
        self.assertIn('best-known', fact['source']['excerpt'])
        self.assertFalse(fact['player_knowledge'])
        self.assertIsNone(fact['date_source'])
        self.assertEqual(self.digest(), before)

    def test_long_quote_is_not_cut_into_a_misleading_partial_assertion(self):
        self.quote('Long description. ' * 35 + 'However this reputation is disputed.')
        packet = self.selector().select(query='canon:' + CLAIM)
        self.assertNotIn('excerpt', packet['facts'][0]['source'])

    def test_source_punctuation_is_normalized_without_changing_snapshot(self):
        self.quote('Description' + chr(0x2014) + 'caveat.')
        before = self.digest()
        packet = self.selector().select(query='canon:' + CLAIM)
        self.assertNotIn(chr(0x2014), check_json(packet))
        self.assertIn('caveat', packet['facts'][0]['source']['excerpt'])
        self.assertEqual(self.digest(), before)

    def test_legacy_sources_without_quote_column_remain_usable(self):
        packet = self.selector().select(query='canon:' + CLAIM)
        self.assertTrue(packet['facts'])
        self.assertNotIn('excerpt', packet['facts'][0]['source'])

    def test_partial_lexical_flag_is_not_an_instruction_to_request_more_memory(self):
        self.assertIn('A false result is never enough to justify memory_request', DIALOGUE)
        self.assertIn('Read source.excerpt', DIALOGUE)
        self.assertIn('do not hide a documentary blockage', DIALOGUE)
        self.assertIn('Secrets stay secret', DIALOGUE)


if __name__ == '__main__':
    unittest.main()
