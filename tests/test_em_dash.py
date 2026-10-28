import unittest

from novel_engine.narration_codex import replace_em_dash


class TiretCadratinTest(unittest.TestCase):
    def test_replaced_throughout_the_structure(self):
        dash=chr(0x2014)
        value={'a':'Texte '+dash+' suite','b':['x'+dash+'y',{'c':dash}],'n':3}
        out,count=replace_em_dash(value)
        self.assertEqual(count,3)
        self.assertNotIn(dash,str(out))
        self.assertEqual(out['a'],'Texte - suite')
        self.assertEqual(out['n'],3)

    def test_unchanged_without_em_dash(self):
        value={'a':'rien'}
        self.assertEqual(replace_em_dash(value),(value,0))


if __name__=='__main__':
    unittest.main()
