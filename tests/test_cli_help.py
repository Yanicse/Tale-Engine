"""The --help of every `novel` command is plain English and says what each option does."""
import contextlib
import io
import unittest

from novel_engine import cli


def help_text(*argv):
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding='utf-8')
    with contextlib.redirect_stdout(out):
        try:
            cli.main(list(argv) + ['--help'])
        except SystemExit:
            pass
        out.flush()
    return ' '.join(raw.getvalue().decode('utf-8').split())


class HelpTexts(unittest.TestCase):
    def test_play_help_reads_as_english(self):
        text = help_text('play')
        for phrase in ('Narrative narrator', 'Resolve one action', 'Structured return', 'Consult', 'registry'):
            self.assertNotIn(phrase, text)
        self.assertIn('Play one action and exit', text)
        self.assertIn('review, in seconds', text)


if __name__ == '__main__':
    unittest.main()
