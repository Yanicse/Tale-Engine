"""The front page of the repository stays true: README, license, contributing guide, CI and package metadata."""
import re
import unittest
from pathlib import Path

from novel_engine import cli

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = set(cli.SCRIPTS) | {'doctor', 'init', 'ingest', 'canon', 'character', 'campaign', 'example', 'validate', 'unblock',
                                 'configure'}
LINK = re.compile(r'(?:\]\(|src=")([^)"#\s]+)')


def read(name):
    return (ROOT / name).read_text(encoding='utf-8')


class FrontPage(unittest.TestCase):
    def test_the_files_a_repository_needs_exist(self):
        for name in ('README.md', 'LICENSE', 'CONTRIBUTING.md', 'CHANGELOG.md', '.github/PULL_REQUEST_TEMPLATE.md',
                     '.github/ISSUE_TEMPLATE/bug_report.md', '.github/ISSUE_TEMPLATE/feature_request.md',
                     'docs/images/banner.svg', 'docs/images/reader.jpg'):
            self.assertTrue((ROOT / name).is_file(), name)
        self.assertTrue(read('LICENSE').startswith('MIT License'))

    def test_every_relative_link_and_image_resolves(self):
        for name in ('README.md', 'CONTRIBUTING.md', 'CHANGELOG.md'):
            for target in LINK.findall(read(name)):
                if target.startswith(('http://', 'https://', 'mailto:')):
                    continue
                self.assertTrue((ROOT / target).exists(), f'{name} points to {target}')

    def test_every_command_in_the_readme_exists(self):
        # Commands are the lines that start with `novel` (code blocks, CI steps) and `novel x` code spans.
        for name in ('README.md', 'CONTRIBUTING.md', '.github/workflows/tests.yml'):
            text = read(name)
            found = re.findall(r'^\s*novel ([a-z]+)\b', text, re.M) + re.findall(r'`novel ([a-z]+)\b', text)
            self.assertTrue(found or name == 'CONTRIBUTING.md', name)
            for command in found:
                self.assertIn(command, COMMANDS, f'{name}: novel {command}')

    def test_the_readme_shows_the_three_command_quickstart_and_the_diagram(self):
        readme = read('README.md')
        for line in ('pip install .', 'novel example', 'novel read'):
            self.assertIn(line, readme)
        self.assertIn('```mermaid', readme)
        for engine in ('claude', 'codex', 'api', 'manual'):
            self.assertIn(engine, readme)
        self.assertIn('Legal note', readme)

    def test_the_package_metadata_points_to_real_files_and_the_badge_matches(self):
        meta = read('pyproject.toml')
        self.assertIn('readme = "README.md"', meta)
        self.assertIn('license = {file = "LICENSE"}', meta)
        self.assertIn('requires-python = ">=3.10"', meta)
        self.assertIn('python-3.10%2B', read('README.md'))
        self.assertIn('## 0.1.0', read('CHANGELOG.md'))

    def test_the_ci_runs_the_suite_and_an_installed_smoke_test_on_three_systems(self):
        ci = read('.github/workflows/tests.yml')
        for needle in ('windows-latest', 'ubuntu-latest', 'macos-latest', 'unittest discover', 'pip install .',
                       'working-directory: ${{ runner.temp }}', 'novel example'):
            self.assertIn(needle, ci)


if __name__ == '__main__':
    unittest.main()
