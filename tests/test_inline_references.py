"""Exact references inside a sentence, budgets and access preserved."""
import sqlite3
import unittest

from novel_engine.indexed_memory import MemoryIndex


class PunctuatedReferenceTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(':memory:')
        self.con.row_factory = sqlite3.Row
        self.addCleanup(self.con.close)
        self.index = MemoryIndex(self.con)
        self.index.initialize()
        self.con.execute('CREATE TABLE exchanges (seq INTEGER,role TEXT,content TEXT)')
        self.ref = 'b2fda081e3ed575289d80a185e47179c'
        self.index._insert(self.ref, 'event', 'Territory limits discussed.', ['narrator'], event_order=1)

    def packet(self, suffix, recipient='narrator'):
        return self.index.packet('', query='Relire '+self.ref+suffix, recipient=recipient, budget=200)

    def test_sentence_punctuation_keeps_exact_reference_priority(self):
        for suffix in ('.', ':', '...', ',', '.)', '.', ''):
            with self.subTest(suffix=suffix):
                packet = self.packet(suffix)
                decision = next(d for d in packet['search']['diagnostic']['sources']
                                if d['reference'] == self.ref)
                self.assertEqual(decision['reason'], 'requested_reference')
                self.assertEqual(packet['proofs'][0]['reference'], self.ref)
                self.assertLessEqual(packet['search']['characters'], 200)

    def test_punctuation_does_not_grant_access(self):
        self.assertEqual(self.packet('.', 'player')['proofs'], [])

    def test_existing_reference_ending_in_dot_stays_exact(self):
        self.index._insert(self.ref+'.', 'event', 'Autre archive exact.', ['narrator'], event_order=2)
        packet = self.packet('.')
        decision = next(d for d in packet['search']['diagnostic']['sources']
                        if d['reference'] == self.ref+'.')
        self.assertEqual(decision['reason'], 'requested_reference')
        original = next(d for d in packet['search']['diagnostic']['sources']
                        if d['reference'] == self.ref)
        self.assertNotIn('requested_reference', original['origins'])
