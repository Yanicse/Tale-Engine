"""Build the sourced canon of a novel pack from its ingested text, one resumable command.

text/vNN.jsonl -> batches within a token budget -> extraction by the chosen engine -> validation
-> canon.sqlite (the tables read by CanonRepository). Every batch is committed on its own, so a
stopped run resumes where it stopped, whatever budget the next run uses: batches are planned over
the pages not yet done. A claim is kept only when its quote is found in the excerpt, and a volume
is closed once every one of its pages has been extracted. The cost is estimated before anything
is sent.
"""
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from . import llm
from .prompts import load as load_prompt

REVISION = '1'
CHARS_PER_TOKEN = 4
OUTPUT_RATIO = 0.3
MAX_STATEMENT = 400
MAX_QUOTE = 300
KINDS = ('character', 'place', 'faction', 'object', 'concept', 'event')

PROMPT = load_prompt('canon_build.prompt')


def tokens(text):
    return len(text) // CHARS_PER_TOKEN + 1


def read_pages(novel):
    for path in sorted((Path(novel) / 'text').glob('v*.jsonl')):
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.strip():
                yield json.loads(line)


def plan(novel, budget=12000, done=frozenset()):
    """Consecutive pages of one volume, grouped under `budget` input tokens; `done` is a set of
    (volume, page) pairs already extracted, which are left out."""
    batches, current, used = [], [], 0
    for page in read_pages(novel):
        if (page['volume'], page['page']) in done:
            continue
        cost = tokens(page['text'])
        if current and (used + cost > budget or current[0]['volume'] != page['volume']
                        or page['page'] != current[-1]['page'] + 1):
            batches.append(current)
            current, used = [], 0
        current.append(page)
        used += cost
    if current:
        batches.append(current)
    return [{'id': f"v{b[0]['volume']:02d}_p{b[0]['page']:04d}_{b[-1]['page']:04d}", 'pages': b,
             'volume': b[0]['volume'], 'first': b[0]['page'], 'last': b[-1]['page'],
             'tokens': sum(tokens(p['text']) for p in b)} for b in batches]


def done_pages(novel):
    """(volume, page) pairs already stored in canon.sqlite; empty before the first build."""
    if not (Path(novel) / 'canon.sqlite').is_file():
        return set()
    with closing(connect(novel)) as con:
        return set(con.execute('SELECT volume,page FROM build_pages'))


def pending_batches(novel, budget=12000):
    """The batches a build run would send now."""
    return plan(novel, budget, done_pages(novel))


def estimate(batches):
    total = sum(b['tokens'] for b in batches)
    return {'batches': len(batches), 'input_tokens': total, 'output_tokens': int(total * OUTPUT_RATIO)}


def make_prompt(batch):
    excerpt = '\n\n'.join(f"[page {p['page']}]\n{p['text']}" for p in batch['pages'])
    return PROMPT.format(limit=MAX_STATEMENT, first=batch['first'], last=batch['last'], excerpt=excerpt)


def connect(novel):
    con = sqlite3.connect(Path(novel) / 'canon.sqlite')
    con.executescript('''
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS entities(entity_id TEXT PRIMARY KEY,kind TEXT,name TEXT,identity_status TEXT);
        CREATE TABLE IF NOT EXISTS aliases(entity_id TEXT,name TEXT);
        CREATE TABLE IF NOT EXISTS canonical_entities(raw_id TEXT PRIMARY KEY,canonical_id TEXT);
        CREATE TABLE IF NOT EXISTS consolidated_usable_claims(claim_id TEXT PRIMARY KEY,canonical_subject_id TEXT,
            statement TEXT,predicate TEXT,world_time TEXT,category TEXT,fact_id TEXT);
        CREATE TABLE IF NOT EXISTS sources(claim_id TEXT,volume INTEGER,pdf_page INTEGER,
            kind TEXT,quote TEXT,image TEXT);
        CREATE TABLE IF NOT EXISTS known_by(claim_id TEXT,entity_id TEXT);
        CREATE VIRTUAL TABLE IF NOT EXISTS consolidated_fts USING fts5(claim_id UNINDEXED,entity_name,aliases,statement,predicate);
        CREATE TABLE IF NOT EXISTS raw_claims(claim_id TEXT PRIMARY KEY,batch_id TEXT,subject TEXT,statement TEXT,
            predicate TEXT,volume INTEGER,page INTEGER,quote TEXT);
        CREATE TABLE IF NOT EXISTS build_batches(batch_id TEXT PRIMARY KEY,status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS build_pages(volume INTEGER,page INTEGER,PRIMARY KEY(volume,page));
    ''')
    if 'quote' not in {row[1] for row in con.execute('PRAGMA table_info(raw_claims)')}:
        con.execute('ALTER TABLE raw_claims ADD COLUMN quote TEXT')
    # Builds made before pages were tracked: recover them from the batch identifiers.
    for (batch_id,) in con.execute("SELECT batch_id FROM build_batches WHERE status='done'").fetchall():
        found = re.fullmatch(r'v(\d+)_p(\d+)_(\d+)', batch_id)
        if found:
            volume, first, last = map(int, found.groups())
            con.executemany('INSERT OR IGNORE INTO build_pages VALUES (?,?)', [(volume, n) for n in range(first, last + 1)])
    con.commit()
    return con


def parse_response(text):
    """JSON object from an answer that may be wrapped in a code fence or a sentence."""
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end < start:
        raise ValueError('no JSON object in the answer')
    return json.loads(text[start:end + 1])


def slug(text):
    return re.sub(r'[^a-z0-9]+', '_', text.casefold()).strip('_')


def normalize(text):
    """Comparison form of a passage: case, quote marks, dashes and spacing are ignored."""
    text = str(text).casefold().translate({0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"', 0x2013: '-', 0x2014: '-'})
    return ' '.join(re.sub(r'[^\w\s]', ' ', text).split())


def validate(data, batch):
    """(entities, claims) cleaned; raises ValueError when nothing usable remains. A claim is kept only
    when its quote is found in a page of the batch, and its page is the one where it was found."""
    if not isinstance(data, dict):
        raise ValueError('the answer is not an object')
    entities = {}
    for item in data.get('entities') or []:
        ident = slug(str(item.get('id') or item.get('name') or ''))
        name = str(item.get('name') or '').strip()
        if ident and name:
            kind = item.get('kind') if item.get('kind') in KINDS else 'concept'
            aliases = [str(a).strip() for a in item.get('aliases') or [] if str(a).strip()]
            entities[ident] = (kind, name, aliases)
    pages = {p['page']: normalize(p['text']) for p in batch['pages']}
    claims = []
    for item in data.get('claims') or []:
        subject, statement = slug(str(item.get('subject') or '')), ' '.join(str(item.get('statement') or '').split())
        quote = ' '.join(str(item.get('quote') or '').split())[:MAX_QUOTE]
        if subject not in entities or not 0 < len(statement) <= MAX_STATEMENT or not normalize(quote):
            continue
        found = [number for number, text in pages.items() if normalize(quote) in text]
        if not found:
            continue
        wanted = item.get('page')
        page = wanted if wanted in found else found[0]
        claims.append((subject, statement, slug(str(item.get('predicate') or 'fact')) or 'fact', page, quote))
    if not claims:
        raise ValueError('no claim with a quote found in the excerpt')
    return entities, claims


def store(con, batch, entities, claims):
    for ident, (kind, name, aliases) in entities.items():
        con.execute('INSERT OR IGNORE INTO entities VALUES (?,?,?,?)', (ident, kind, name, 'named'))
        for alias in [name] + aliases:
            if not con.execute('SELECT 1 FROM aliases WHERE entity_id=? AND name=?', (ident, alias)).fetchone():
                con.execute('INSERT INTO aliases VALUES (?,?)', (ident, alias))
    for number, (subject, statement, predicate, page, quote) in enumerate(claims, 1):
        claim_id = f"{batch['id']}:c{number}"
        con.execute('INSERT OR REPLACE INTO raw_claims VALUES (?,?,?,?,?,?,?,?)',
                    (claim_id, batch['id'], subject, statement, predicate, batch['volume'], page, quote))
    con.execute('INSERT OR REPLACE INTO build_batches VALUES (?,?)', (batch['id'], 'done'))
    con.executemany('INSERT OR IGNORE INTO build_pages VALUES (?,?)', [(p['volume'], p['page']) for p in batch['pages']])


def entity_groups(con):
    """raw entity id -> canonical id. Entities with the same name merge; an alias that is the name
    of another entity of the same kind merges them too. The earliest entity is the canonical one."""
    rows = con.execute('SELECT entity_id,kind,name FROM entities ORDER BY rowid').fetchall()
    order = {ident: number for number, (ident, _, _) in enumerate(rows)}
    kinds = {ident: kind for ident, kind, _ in rows}
    parent = {ident: ident for ident in order}

    def find(ident):
        while parent[ident] != ident:
            parent[ident] = parent[parent[ident]]
            ident = parent[ident]
        return ident

    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            keep, drop = (a, b) if order[a] < order[b] else (b, a)
            parent[drop] = keep

    by_name = {}
    for ident, _, name in rows:
        key = slug(name)
        if key in by_name:
            union(ident, by_name[key])
        else:
            by_name[key] = ident
    for ident, alias in con.execute('SELECT entity_id,name FROM aliases').fetchall():
        other = by_name.get(slug(alias))
        if other and other != ident and kinds.get(other) == kinds.get(ident):
            union(ident, other)
    return {ident: find(ident) for ident in order}


def consolidate(con):
    """Merge entities, drop duplicate statements, rebuild the search index; returns the claim count."""
    canonical = entity_groups(con)
    members = {}
    for ident, target in canonical.items():
        members.setdefault(target, []).append(ident)
    for table in ('canonical_entities', 'consolidated_usable_claims', 'sources', 'consolidated_fts'):
        con.execute(f'DELETE FROM {table}')
    con.executemany('INSERT INTO canonical_entities VALUES (?,?)', canonical.items())
    seen = set()
    for claim_id, subject, statement, predicate, volume, page, quote in con.execute(
            'SELECT claim_id,subject,statement,predicate,volume,page,quote FROM raw_claims ORDER BY rowid').fetchall():
        target = canonical.get(subject, subject)
        key = (target, slug(statement))
        if key in seen:
            continue
        seen.add(key)
        con.execute('INSERT INTO consolidated_usable_claims VALUES (?,?,?,?,?,?,?)',
                    (claim_id, target, statement, predicate, None, 'canon', claim_id))
        con.execute('INSERT INTO sources VALUES (?,?,?,?,?,?)', (claim_id, volume, page, 'text', quote, None))
        name = con.execute('SELECT name FROM entities WHERE entity_id=?', (target,)).fetchone()[0]
        names = []
        for member in members.get(target, [target]):
            names += [a for (a,) in con.execute('SELECT name FROM aliases WHERE entity_id=?', (member,))]
        con.execute('INSERT INTO consolidated_fts VALUES (?,?,?,?,?)',
                    (claim_id, name, ' '.join(dict.fromkeys(names)), statement, predicate))
    con.executemany('INSERT OR REPLACE INTO metadata VALUES (?,?)',
                    [('revision', REVISION), ('consolidation_canon_revision', REVISION)])
    return len(seen)


def close_volumes(con, novel):
    """A volume is closed when every one of its pages was extracted; recorded in the metadata."""
    totals = {}
    for page in read_pages(novel):
        totals.setdefault(page['volume'], set()).add(page['page'])
    done = {}
    for volume, page in con.execute('SELECT volume,page FROM build_pages'):
        done.setdefault(volume, set()).add(page)
    status = {volume: ('closed' if pages <= done.get(volume, set()) else 'open') for volume, pages in sorted(totals.items())}
    con.executemany('INSERT OR REPLACE INTO metadata VALUES (?,?)',
                    [(f'volume_{volume}', state) for volume, state in status.items()])
    return status


def build(novel, engine, model=None, budget=12000, limit=None, log=print):
    """Run (or resume) the extraction; returns a status dict."""
    novel = Path(novel)
    if not any(True for _ in read_pages(novel)):
        raise SystemExit('No ingested text: run `novel ingest` first.')
    work = novel / 'work'
    work.mkdir(exist_ok=True)
    pending_manual, failed, ran = [], [], 0
    with closing(connect(novel)) as con:
        batches = plan(novel, budget, set(con.execute('SELECT volume,page FROM build_pages')))
        for batch in batches:
            if limit is not None and ran >= limit:
                break
            answer = work / (batch['id'] + '.response.json')
            try:
                if engine == 'manual':
                    (work / (batch['id'] + '.prompt.md')).write_text(make_prompt(batch), encoding='utf-8')
                    if not answer.is_file():
                        pending_manual.append(batch['id'])
                        continue
                    raw = answer.read_text(encoding='utf-8')
                else:
                    raw = llm.complete(engine, make_prompt(batch), model)
                    answer.write_text(raw, encoding='utf-8')
                entities, claims = validate(parse_response(raw), batch)
                with con:
                    store(con, batch, entities, claims)
                ran += 1
                log(f"  {batch['id']}: {len(claims)} claims")
            except (ValueError, llm.ModelUnavailable) as error:
                failed.append(batch['id'])
                log(f"  {batch['id']}: failed ({error})")
        with con:
            claims = consolidate(con)
            volumes = close_volumes(con, novel)
    return {'batches': len(batches), 'remaining': len(batches) - ran, 'failed': failed,
            'awaiting_manual_answer': pending_manual, 'claims': claims, 'volumes': volumes}
