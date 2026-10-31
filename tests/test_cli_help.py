"""The --help of every `novel` command is plain English and says what each option does."""
import argparse
import contextlib
import importlib
import io
import unittest
from unittest import mock

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


def parser_of(main):
    """The parser a command builds, caught before it reads any argument."""
    seen = {}

    def caught(parser, *args, **kwargs):
        seen['parser'] = parser
        raise SystemExit(0)
    with mock.patch.object(argparse.ArgumentParser, 'parse_args', caught):
        try:
            main([]) if main.__code__.co_argcount else main()
        except SystemExit:
            pass
    return seen['parser']


def undocumented(parser, prefix):
    missing = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            described = {choice.dest for choice in action._choices_actions if choice.help}
            for name, sub in action.choices.items():
                if name not in described:
                    missing.append(f'{prefix} {name}')
                missing += undocumented(sub, f'{prefix} {name}')
        elif action.help is None and action.dest != 'help' and action.nargs != argparse.REMAINDER:
            missing.append(f'{prefix} {(action.option_strings or [action.dest])[0]}')
    return missing


class HelpTexts(unittest.TestCase):
    def test_play_help_reads_as_english(self):
        text = help_text('play')
        for phrase in ('Narrative narrator', 'Resolve one action', 'Structured return', 'Consult', 'registry'):
            self.assertNotIn(phrase, text)
        self.assertIn('Play one action and exit', text)
        self.assertIn('review, in seconds', text)

    def test_every_subcommand_and_option_has_a_help_text(self):
        missing = []
        mains = [('novel', cli.main)] + [(f'novel {name}', importlib.import_module(
            'novel_engine.scripts.' + script.removesuffix('.py')).main) for name, script in cli.SCRIPTS.items()]
        for prefix, main in mains:
            missing += undocumented(parser_of(main), prefix)
        self.assertEqual(missing, [])


if __name__ == '__main__':
    unittest.main()
