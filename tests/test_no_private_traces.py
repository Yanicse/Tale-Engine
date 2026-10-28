"""Guards the public repository against personal or copyrighted leftovers.

Generic checks always run (CI included): no personal home paths, no real e-mail
addresses, no campaign databases or source books. An optional private term list
(`.private_terms.txt` at the repository root, or the PRIVATE_TERMS_FILE
environment variable) adds a name check; it stays out of git so the names
themselves are never published.
"""
import hashlib
import os
import re
import unicodedata
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HASHES_FILE = ROOT / 'tests' / 'private_term_hashes.txt'
MAX_WORDS = 4
TERMS_FILE = Path(os.environ.get('PRIVATE_TERMS_FILE') or ROOT / '.private_terms.txt')
SKIPPED_DIRS = {'.git', '__pycache__'}
SKIPPED_FILES = {'.private_terms.txt', '.export_public.json'}
BINARY_SUFFIXES = {'.ttf', '.otf', '.woff', '.woff2', '.png', '.jpg', '.jpeg', '.gif', '.ico', '.webp'}
FORBIDDEN_SUFFIXES = ('.sqlite', '.sqlite-wal', '.sqlite-shm', '.sqlite-journal', '.db',
                      '.pdf', '.epub', '.mobi', '.azw3', '.zip', '.lock')
HOME_PATH = re.compile(
    r'(?i)\b[a-z]:(?:\\\\|\\|/)+users(?:\\\\|\\|/)+(?![<{%$]|public\b|default\b)[^\\/\s"\'`]+'
    r'|/(?:home|Users)/(?![<{$])[A-Za-z0-9._-]+/')
EMAIL = re.compile(r'\b[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})\b')
ALLOWED_EMAIL_DOMAINS = re.compile(r'(?i)^(?:.+\.)?(?:example\.(?:com|org|net)|invalid|test|localhost)$')
# Third-party license and credit files keep their authors' published contact lines.
THIRD_PARTY_NOTICE = re.compile(r'(?i)(?:^|/)(?:licen[cs]e|credits|ofl)[^/]*$')
LIMIT = 40


def fold(text):
    """Lowercase and strip accents so 'Chloé' and 'chloe' compare equal."""
    decomposed = unicodedata.normalize('NFKD', text.casefold())
    return ''.join(ch for ch in decomposed if not unicodedata.combining(ch))


def repo_files():
    for path in sorted(ROOT.rglob('*')):
        rel = path.relative_to(ROOT)
        if any(part in SKIPPED_DIRS or part.startswith('.tmp-') for part in rel.parts):
            continue
        if path.is_file() and rel.name not in SKIPPED_FILES:
            yield path, rel.as_posix()


def text_lines(path):
    if path.suffix.lower() in BINARY_SUFFIXES:
        return []
    data = path.read_bytes()
    if b'\0' in data[:8192]:
        return []
    try:
        return data.decode('utf-8').splitlines()
    except UnicodeDecodeError:
        return []


def load_terms(path):
    terms = []
    for line in path.read_text(encoding='utf-8').splitlines():
        term = fold(line.strip())
        if term and not term.startswith('#'):
            words = [re.escape(word) for word in re.split(r'[\s_-]+', term) if word]
            terms.append((term, re.compile(r'(?<![0-9a-z])' + r'[\s_-]*'.join(words) + r'(?![0-9a-z])')))
    return terms


def report(findings):
    shown = '\n'.join(findings[:LIMIT])
    more = f'\n... and {len(findings) - LIMIT} more' if len(findings) > LIMIT else ''
    return f'{len(findings)} finding(s):\n{shown}{more}'


def load_hashes(path):
    return {line.strip() for line in path.read_text(encoding='utf-8').splitlines()
            if line.strip() and not line.startswith('#')}


def hashed_hits(line, hashes):
    words = re.findall(r'[0-9a-z]+', fold(line))
    return sorted({' '.join(words[i:i + n]) and hashlib.sha256(' '.join(words[i:i + n]).encode()).hexdigest()[:8]
                   for n in range(1, MAX_WORDS + 1) for i in range(len(words) - n + 1)
                   if hashlib.sha256(' '.join(words[i:i + n]).encode()).hexdigest() in hashes})


class NoPrivateTracesTest(unittest.TestCase):
    def test_no_private_files(self):
        found = [rel for _, rel in repo_files() if rel.lower().endswith(FORBIDDEN_SUFFIXES)]
        self.assertEqual(found, [], report(found))

    def test_no_personal_paths_or_emails(self):
        findings = []
        for path, rel in repo_files():
            for number, line in enumerate(text_lines(path), 1):
                for match in HOME_PATH.finditer(line):
                    findings.append(f'{rel}:{number}: home path {match.group(0)!r}')
                for match in EMAIL.finditer(line):
                    if not (ALLOWED_EMAIL_DOMAINS.match(match.group(1)) or THIRD_PARTY_NOTICE.search(rel)):
                        findings.append(f'{rel}:{number}: e-mail address')
        self.assertEqual(findings, [], report(findings))

    def test_no_hashed_private_terms(self):
        hashes = load_hashes(HASHES_FILE)
        findings = []
        for path, rel in repo_files():
            if rel == HASHES_FILE.relative_to(ROOT).as_posix():
                continue
            for number, line in enumerate(text_lines(path) + [rel.replace('/', ' ')], 1):
                for digest in hashed_hits(line, hashes):
                    findings.append(f'{rel}:{number}: forbidden term (hash {digest})')
        self.assertEqual(findings, [], report(findings))

    def test_no_private_terms(self):
        if not TERMS_FILE.is_file():
            self.skipTest('no private term list (.private_terms.txt or PRIVATE_TERMS_FILE)')
        terms = load_terms(TERMS_FILE)
        findings = []
        for path, rel in repo_files():
            for term, pattern in terms:
                if pattern.search(fold(rel)):
                    findings.append(f'{rel}: file name contains {term!r}')
            for number, line in enumerate(text_lines(path), 1):
                folded = fold(line)
                hits = sorted({term for term, pattern in terms if pattern.search(folded)})
                if hits:
                    findings.append(f'{rel}:{number}: {", ".join(hits)}')
        self.assertEqual(findings, [], report(findings))


if __name__ == '__main__':
    unittest.main()
