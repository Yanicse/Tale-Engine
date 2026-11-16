"""The suite never works in the real novels/ and campaigns/ folders: tests that need a home take a throwaway one."""
from pathlib import Path
import re
import unittest

from novel_engine import cli, contracts, resume
from home import isolated_home

TESTS = Path(__file__).resolve().parent
REAL_HOME = re.compile(r"\b(ROOT|HOME)\s*/\s*'(campaigns|novels)\b|\bresume\.ACTIVE\b|'campaigns/active\.json'")


class Redirected(unittest.TestCase):
    def test_the_home_is_redirected_for_one_test(self):
        home = isolated_home(self)
        self.assertNotEqual(home, self.real)
        self.assertEqual((contracts.ROOT, cli.ROOT, resume.ROOT), (home, home, home))
        self.assertEqual(cli.NOVELS, home / 'novels')
        self.assertEqual(resume.ACTIVE, home / 'campaigns' / 'active.json')
        self.assertEqual(resume.active_campaign.__defaults__, (home / 'campaigns' / 'active.json',))
        with self.assertRaises(resume.ActiveCampaignError):
            resume.active_campaign()

    def setUp(self):
        self.real = contracts.ROOT
        self.addCleanup(self.check_restored)

    def check_restored(self):
        # Registered first, so it runs after the cleanups of isolated_home.
        self.assertEqual((contracts.ROOT, cli.ROOT, resume.ROOT), (self.real, self.real, self.real))
        self.assertEqual(cli.NOVELS, self.real / 'novels')
        self.assertEqual(resume.active_campaign.__defaults__, (self.real / 'campaigns' / 'active.json',))
        self.assertEqual(list(self.real.glob('.tmp-home-*')), [])


class NoTestNamesTheRealHome(unittest.TestCase):
    def test_test_files_do_not_reach_into_the_real_folders(self):
        found = []
        for path in sorted(TESTS.glob('*.py')):
            if path.name in ('home.py', 'test_home.py'):
                continue
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                if REAL_HOME.search(line):
                    found.append(f'{path.name}:{number}: {line.strip()}')
        self.assertEqual(found, [], 'use isolated_home (tests/home.py) instead of the real home')


if __name__ == '__main__':
    unittest.main()
