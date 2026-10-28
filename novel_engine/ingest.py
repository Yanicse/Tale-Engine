"""Extract the text of a book (TXT, EPUB, PDF) into numbered pages of a novel pack, standard library only
(PDF needs the optional `pypdf` package)."""
import json
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

PAGE_CHARS = 3000
BLOCK_TAGS = {'p', 'div', 'br', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'tr', 'section'}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        self.skip += tag in ('script', 'style')
        if tag in BLOCK_TAGS:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.skip:
            self.skip -= 1
        if tag in BLOCK_TAGS:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(markup):
    parser = _Text()
    parser.feed(markup)
    return re.sub(r'\n\s*\n+', '\n\n', re.sub(r'[ \t]+', ' ', ''.join(parser.parts))).strip()


def paginate(text, size=PAGE_CHARS):
    """Cut at paragraph boundaries into pages of about `size` characters."""
    pages, current = [], ''
    for paragraph in re.split(r'\n\s*\n', text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if current and len(current) + len(paragraph) > size:
            pages.append(current)
            current = ''
        current += ('\n\n' if current else '') + paragraph
    if current:
        pages.append(current)
    return pages


def read_epub(path):
    with zipfile.ZipFile(path) as book:
        container = book.read('META-INF/container.xml').decode('utf-8')
        opf = re.search(r'full-path="([^"]+)"', container).group(1)
        package = book.read(opf).decode('utf-8')
        base = PurePosixPath(opf).parent
        manifest = {}
        for tag in re.findall(r'<item\b[^>]*>', package):
            ident, href = re.search(r'\bid="([^"]+)"', tag), re.search(r'\bhref="([^"]+)"', tag)
            if ident and href:
                manifest[ident.group(1)] = href.group(1)
        order = [manifest[i] for i in re.findall(r'<itemref\b[^>]*idref="([^"]+)"', package) if i in manifest]
        chapters = [html_to_text(book.read(str(base / href)).decode('utf-8', 'replace')) for href in order]
    return '\n\n'.join(c for c in chapters if c)


def read_pdf_pages(path):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise SystemExit('PDF extraction needs the optional dependency: pip install "tale-engine[pdf]"')
    return [(page.extract_text() or '').strip() for page in PdfReader(str(path)).pages]


def extract_pages(path):
    """List of page texts, in reading order; PDF keeps its own page numbers."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == '.pdf':
        return read_pdf_pages(path)
    if suffix == '.epub':
        return paginate(read_epub(path))
    if suffix in ('.txt', '.md'):
        return paginate(path.read_text(encoding='utf-8', errors='replace'))
    raise SystemExit('Unsupported format (use .txt, .md, .epub or .pdf): ' + path.name)


def ingest(novel, source, volume=None):
    """Write text/vNN.jsonl in the novel pack; returns (volume, pages)."""
    text_dir = Path(novel) / 'text'
    text_dir.mkdir(parents=True, exist_ok=True)
    if volume is None:
        volume = len(list(text_dir.glob('v*.jsonl'))) + 1
    pages = extract_pages(source)
    if not any(pages):
        raise SystemExit('No text found in ' + str(source))
    with (text_dir / f'v{volume:02d}.jsonl').open('w', encoding='utf-8', newline='\n') as handle:
        for number, text in enumerate(pages, 1):
            handle.write(json.dumps({'volume': volume, 'page': number, 'text': text}, ensure_ascii=False) + '\n')
    return volume, len(pages)
