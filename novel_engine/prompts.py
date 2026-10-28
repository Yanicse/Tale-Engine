"""Prompt texts, kept as files so they can be read, reviewed and translated without touching code.

novel_engine/prompts/<language>/<module>.<name>.md, for example prompts/en/instructions.opening.md.
A text is read exactly as written (placeholders such as {word_target} are filled by the caller with
str.format). A language folder may translate only some texts: the others fall back to English.
"""
from functools import lru_cache
from pathlib import Path

FOLDER = Path(__file__).resolve().parent / 'prompts'
DEFAULT_LANGUAGE = 'en'


def languages():
    return sorted(p.name for p in FOLDER.iterdir() if p.is_dir())


def names(language=DEFAULT_LANGUAGE):
    return sorted(p.name[:-3] for p in (FOLDER / language).glob('*.md'))


@lru_cache(maxsize=None)
def load(name, language=DEFAULT_LANGUAGE):
    """Text of a prompt in a language, falling back to English."""
    for folder in dict.fromkeys((language, DEFAULT_LANGUAGE)):
        path = FOLDER / folder / (name + '.md')
        if path.is_file():
            return path.read_text(encoding='utf-8')
    raise FileNotFoundError(f'No prompt named {name!r} in {FOLDER}')
