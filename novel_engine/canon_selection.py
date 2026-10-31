"""Local documentary selection, private and bounded, in an immutable canon."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re

from .contracts import ROOT, check_json
from .memory import CanonRepository, words, memory_terms
from .prompts import load as load_prompt


BUDGETS={'narrator':3000,'planner':6000}
LIMITS={'narrator':5,'planner':10}
MAX_CANDIDATES=96
MAX_QUERIES=3
GENERAL_PREDICATES={'species','nature','creator','rank_hierarchy','geography'}
STOP=set(words(('village town road forest gate guard character group man woman human day night form '
                'world life level power system object unknown guild kingdom table house path service '
                'request traveler customer speak speaks search seek know knowing inspect greet greeting '
                'hello observe watch look find place original')))
TOPICS={
    'guild':('guild','adventurers'),
    'rank':('hierarchy','ranks','promotion','adventurers'),
    'ranks':('hierarchy','ranks','promotion','adventurers'),
    'promotion':('promotion','ranks','guild'),
    'character':('karma','alignment','loyalty','motivation'),
    'personality':('karma','alignment','loyalty','motivation'),
    'abilities':('ability','level','spell','sheet'),
    'ability':('ability','level','spell','sheet'),
    'magic':('magic','spell','effect'),
    'escort':('escort','cart','transport'),
    'cart':('cart','transport'),
    'grain':('grain','merchant'),
    'economy':('currency','trade','price'),
    'currency':('currency','trade','coins'),
    'geography':('geography','forest','village')}
for _topic in ('reputation','fame','famous','known'):
    TOPICS[_topic]=('reputation','fame','famous','achievements','adventurers')
INSTRUCTION = load_prompt('canon_selection.instruction')


def normalized(text):
    return ' '.join(words(text.replace('_',' ')))


@lru_cache(maxsize=4)
def lexicon(snapshot,revision,mtime,size):
    """Cache the names only, never all the facts of the volumes."""
    repository=CanonRepository(snapshot,revision)
    with repository.connection() as con:
        rows=con.execute('''SELECT e.entity_id,e.kind,e.name,a.name AS alias,
            COALESCE(m.canonical_id,e.entity_id) AS canonical_id FROM entities e
            LEFT JOIN aliases a ON a.entity_id=e.entity_id
            LEFT JOIN canonical_entities m ON m.raw_id=e.entity_id
            WHERE e.identity_status='named' ORDER BY e.entity_id,a.name''')
        names={}
        for row in rows:
            item=names.setdefault(row['canonical_id'],{'id':row['canonical_id'],
                'name':row['name'],'type':row['kind'],'alias':set()})
            for name in (row['name'],row['alias']):
                if name:
                    value=normalized(name)
                    if len(value)>=4 and value not in STOP:item['alias'].add(value)
        return tuple({**item,'alias':tuple(sorted(item['alias']))} for item in names.values())


def receipt_tables(con):
    con.execute('''CREATE TABLE IF NOT EXISTS canon_selection_contexts (
        request_id TEXT NOT NULL,context_id TEXT NOT NULL,role TEXT NOT NULL,
        state_version INTEGER NOT NULL,canon_revision INTEGER NOT NULL,payload TEXT NOT NULL,
        PRIMARY KEY(request_id,context_id,role))''')


def save_context(con,request_id,state,role,packet):
    if packet is None:return
    receipt_tables(con)
    text=check_json(packet)
    identifier=hashlib.sha256(text.encode('utf-8')).hexdigest()
    con.execute('INSERT OR IGNORE INTO canon_selection_contexts VALUES (?,?,?,?,?,?)',
        (request_id,identifier,role,state['state_version'],state['versions']['canon_revision'],text))


def selected_references(con,request_id,state):
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name='canon_selection_contexts'").fetchone():return set()
    refs=set()
    for row in con.execute('SELECT payload FROM canon_selection_contexts WHERE request_id=? '
        'AND state_version=? AND canon_revision=?',(request_id,state['state_version'],state['versions']['canon_revision'])):
        refs.update(f['reference'] for f in json.loads(row[0])['facts'])
    return refs


def validate_canon_causes(con,proposal,state,existing):
    """A source from the book is not enough to realize an episode in the campaign."""
    allowed=selected_references(con,proposal['request_id'],state)|set(existing)
    local=set(state['applied_event_ids'])|{'action:'+proposal['request_id']}
    local.update(ref for ref in existing if ref.startswith(('agenda:','plan:')))
    for event in proposal['events']:
        canonical=[r for r in event['causes'] if r.startswith('canon:')]
        if canonical and any(ref not in allowed for ref in canonical):
            raise ValueError('Canonical reference missing from the authorized context.')
        if canonical and not any(ref in local for ref in event['causes']):
            raise ValueError('The novel alone does not realize a campaign event; a local cause is required.')
        local.add(event['event_id'])


def preparation_text(config):
    relative=config.get('private_preparation')
    if not relative:return ''
    path=(ROOT/relative).resolve()
    if not path.is_relative_to(ROOT.resolve()):raise ValueError('Canonical preparation outside the project refused.')
    return path.read_text(encoding='utf-8')


def public_reference_path(config):
    relative=config.get('public_knowledge_reference')
    if not relative and config.get('private_preparation'):
        relative=str(Path(config['private_preparation']).with_name('KNOWLEDGE_PUBLIC.json'))
    if not relative:return None
    path=(ROOT/relative).resolve()
    if not path.is_relative_to(ROOT.resolve()):raise ValueError('Public sheet outside the project refused.')
    return path if path.is_file() else None


def public_references(config, text):
    """Sourced private markers, never an automatic attribution of knowledge."""
    path=public_reference_path(config)
    if path is None:return {}
    sheet=json.loads(path.read_text(encoding='utf-8'))
    if sheet.get('version')!=1:raise ValueError('Unknown public sheet version.')
    terms=set(words(text))
    return {item['reference'].removeprefix('canon:'):item for item in sheet['markers']
            if terms & set(item['subjects'])}


class CanonSelector:
    def __init__(self,state,config):
        self.state,self.config=state,config
        self.repository=CanonRepository(state['versions']['canon_snapshot'],state['versions']['canon_revision'])
        info=self.repository.path.stat()
        self.names=lexicon(str(self.repository.path.resolve()),self.repository.revision,info.st_mtime_ns,info.st_size)

    def subjects(self,texts):
        found={}
        for priority,text in enumerate(texts):
            text=normalized(text)
            padded=' '+text+' '
            tokens=set(text.split())
            for item in self.names:
                matching=[a for a in item['alias'] if ' '+a+' ' in padded or
                          (len(a.replace(' ',''))>=5 and a.replace(' ','') in tokens)]
                if not matching:continue
                score=(6-priority)*10+max(len(a.split()) for a in matching)
                if item['id'] not in found or score>found[item['id']]['score']:
                    found[item['id']]={**item,'score':score,'match':max(matching,key=len)}
        return sorted(found.values(),key=lambda i:(-i['score'],i['id']))[:8]

    def select(self,action='',scene='',position='',query='',projects=(),role='narrator',existing_text=''):
        if role not in BUDGETS:raise ValueError('Invalid canonical role.')
        action=(query+' '+action)[:1600]
        scene=scene[-1600:]
        project_text=' '.join(' '.join(str(p.get(k,'')) for k in ('actor','location','title','actor','place','title','goal'))
                              for p in projects)[:6000] if role=='planner' else ''
        texts=[action,position,scene,project_text]
        subjects=self.subjects(texts)
        explicit_subjects={s['id'] for s in subjects if s['score']>=60}
        # The subject of a precise reminder takes precedence over the places and institutions of the context.
        query_focus=re.split('\\b(?:set aside|exclude|ignore|without importing|distinguish)\\b',normalized(query),maxsplit=1)[0]
        requested=self.subjects([query_focus]) if query else []
        specific=[s for s in requested if s['type']!='place' and not re.search(
            r'\b(kingdom|empire|theocracy)\b',normalized(s['name']))]
        focused=specific or requested
        reputation=bool(set(words(action)) & {'famous','reputation','fame','known','star'})
        if reputation and not specific:
            focused=[]
            explicit_subjects=set()
        if focused:
            explicit_subjects={s['id'] for s in focused}
            subjects=focused+[s for s in subjects if s['id'] not in explicit_subjects]
        explicit_aliases=[a for s in subjects if s['id'] in explicit_subjects for a in s['alias']]
        explicit_terms=memory_terms(action)-STOP
        context_terms=memory_terms(scene+' '+position)-STOP
        expanded=set()
        for word in explicit_terms:
            expanded.update(TOPICS.get(word,()))
        for word in context_terms & {'guild','rank','ranks','promotion','cart','escort'}:
            expanded.update(TOPICS.get(word,()))
        facets=set().union(*(set(TOPICS.get(t,())) for t in explicit_terms)) if explicit_terms else set()
        keyword_terms=sorted((explicit_terms|expanded)-{'canon'})[:18]
        if not keyword_terms and not subjects:
            keyword_terms=sorted(context_terms)[:8]
        expressions=[]
        for group in ([s for s in subjects if s['id'] in explicit_subjects],
                      [s for s in subjects if s['id'] not in explicit_subjects]):
            if group:
                clauses=[]
                for item in group:
                    phrase='"'+item['match']+'"'
                    clauses.extend(['entity_name:'+phrase,'aliases:'+phrase])
                expressions.append(' OR '.join(clauses))
        if keyword_terms:
            expressions.append(' OR '.join('"'+t+'"' for t in keyword_terms))
        candidates={};queries=0
        public=public_references(self.config,action)
        with self.repository.connection() as con:
            has_quotes=any(row['name']=='quote' for row in con.execute('PRAGMA table_info(sources)'))
            # A precise reference is also a local search, never a remote tool.
            exact=re.findall(r'\b(?:canon:)?(v\d{2}_p\d{4}_\d{4}:c\d+)\b',query)[:5]
            for ref in dict.fromkeys(exact+list(public)):
                row=con.execute('SELECT * FROM consolidated_usable_claims WHERE claim_id=?',(ref,)).fetchone()
                if row:candidates[ref]=dict(row)
            for expression in expressions[:MAX_QUERIES]:
                rows=con.execute('''SELECT c.*,f.rank AS fts_rank FROM consolidated_fts f
                    JOIN consolidated_usable_claims c ON c.claim_id=f.claim_id
                    WHERE consolidated_fts MATCH ? ORDER BY rank LIMIT 48''',(expression,)).fetchall()
                queries+=1
                for row in rows:
                    if len(candidates)>=MAX_CANDIDATES:break
                    candidates.setdefault(row['claim_id'],dict(row))
            subject_scores={s['id']:s['score'] for s in subjects}
            existing_refs=set(re.findall(r'\b(v\d{2}_p\d{4}_\d{4}:c\d+)\b',existing_text))
            existing_normalized=normalized(existing_text)
            ranked=[]
            for claim in candidates.values():
                if claim['claim_id'] in existing_refs:continue
                sentence=normalized(claim['statement'])
                if len(sentence)>35 and sentence in existing_normalized:continue
                terms=set(words(claim['statement']+' '+claim['predicate']))
                if claim['claim_id'] not in exact and explicit_subjects and claim['canonical_subject_id'] not in explicit_subjects and not any(
                        ' '+a+' ' in ' '+sentence+' ' for a in explicit_aliases):
                    if role=='narrator' or claim['canonical_subject_id'] not in subject_scores:continue
                rank_focus=bool(explicit_terms & {'rank','ranks','promotion'})
                guild_focus='guild' in explicit_terms and ('adventurer' in normalized(scene+' '+position) or rank_focus)
                if claim['claim_id'] not in exact and not focused:
                    if rank_focus and not terms & {'rank','ranks','promotion','hierarchy','adventurer','adventurers'}:continue
                    if guild_focus and not terms & {'adventurer','adventurers','receptionist','copper','mithril','promotion'}:continue
                subject_score=subject_scores.get(claim['canonical_subject_id'],0)
                direct=len(terms & explicit_terms)
                thematic=len(terms & (expanded|facets))
                if explicit_terms and not explicit_subjects and not direct and not thematic and claim['claim_id'] not in exact and claim['claim_id'] not in public:
                    if role=='narrator':continue
                if not subject_score and not direct and not thematic and claim['claim_id'] not in exact and claim['claim_id'] not in public:continue
                score=subject_score+direct*12+thematic*8
                if focused and claim['canonical_subject_id'] in explicit_subjects:score+=200
                if rank_focus and claim['predicate']=='rank_hierarchy':score+=100
                if facets and subject_score and terms & facets:score+=20
                if claim['predicate'] in GENERAL_PREDICATES:score+=12
                if role=='narrator' and claim['category']=='testimony':score-=5
                if claim['claim_id'] in exact:score+=1000
                if claim['claim_id'] in public:score+=110
                ranked.append((score,claim))
            ranked.sort(key=lambda r:(-r[0],r[1]['claim_id']))
            packet={'revision':self.repository.revision,'facts':[],
                    'search':{'budget':BUDGETS[role],'characters':0,'candidates':len(candidates),
                                 'requests':queries,'model_calls':0},'instruction':INSTRUCTION}
            seen=set();per_subject={}
            for _,claim in ranked:
                subject=claim['canonical_subject_id']
                identity=claim['fact_id'] or normalized(claim['statement'])
                maximum=(5 if role=='narrator' and subject in explicit_subjects else 3 if role=='narrator' else 4)
                if identity in seen or per_subject.get(subject,0)>=maximum:continue
                if len(claim['statement'])>650:continue
                name=con.execute('SELECT name,kind FROM entities WHERE entity_id=?',(subject,)).fetchone()
                source=con.execute('SELECT volume,pdf_page'+(',quote' if has_quotes else '')+
                                   ' FROM sources WHERE claim_id=? ORDER BY volume,pdf_page LIMIT 1',
                                   (claim['claim_id'],)).fetchone()
                if not source:continue
                access=self.config.get('canon_access',{}).get(claim['claim_id'],{})
                usage=('anchor_valid' if access.get('validated_for_start') and 'narrator' in access.get('audience',[]) else
                       'novel_testimony' if claim['category']=='testimony' else
                       'documentary_marker' if claim['predicate'] in GENERAL_PREDICATES else 'novel_episode')
                item={'reference':'canon:'+claim['claim_id'],'subject':name['name'] if name else subject,
                      'type':name['kind'] if name else 'unknown','statement':claim['statement'],
                      'category':claim['category'],'usage':usage,'date_source':claim['world_time'],
                      'source':{'volume':source['volume'],'page_pdf':source['pdf_page']},
                      'audience':['narrator'],'player_knowledge':False}
                if claim['claim_id'] in public:
                    note=public[claim['claim_id']]
                    item['public_marker']={'status':note['status'],'limit':note['limit']}
                    if note['status']=='descriptive_marker' and claim['predicate'] in ('composition','direction'):
                        item['usage']='documentary_marker'
                # The summary of an assertion may omit a decisive detail of its proof.
                # Provide only a short whole quotation, without loading the volume or truncating a caveat.
                if has_quotes and source['quote'] and len(source['quote'])<=450:
                    item['source']['excerpt']=source['quote']
                if claim['category']=='testimony':
                    speaker=claim.get('canonical_speaker_id') or claim.get('speaker_id')
                    row=con.execute('SELECT name FROM entities WHERE entity_id=?',(speaker,)).fetchone() if speaker else None
                    item['speaker']=row[0] if row else None
                packet['facts'].append(item)
                self._size(packet)
                if packet['search']['characters']>BUDGETS[role]:
                    packet['facts'].pop();self._size(packet);continue
                seen.add(identity);per_subject[subject]=per_subject.get(subject,0)+1
                if len(packet['facts'])>=LIMITS[role]:break
            self._size(packet)
            refs={f['reference'].removeprefix('canon:') for f in packet['facts']}
            matched_subjects={f['subject'] for f in packet['facts']}
            matched=set(exact)<=refs if exact else bool(focused and all(
                s['name'] in matched_subjects or any(' '+a+' ' in ' '+normalized(f['statement'])+' '
                    for a in s['alias'] for f in packet['facts']) for s in focused))
            # Finding the right name does not prove its reputation, rank or chronology.
            query_terms=set(words(query_focus))
            facets_to_check=[
                ({'reputation','fame','famous','known'},
                 {'reputation','fame','renowned','famous','known','respect','achievements'}),
                ({'leader','leaders'},{'leads','leader'}),
                ({'rank','ranks'},{'rank','ranks','adamantite','orichalcum','mithril','copper'})]
            evidence=set(words(' '.join(f['statement'] for f in packet['facts'])))
            facets_ok=all(not query_terms&asked or bool(evidence&needed) for asked,needed in facets_to_check)
            if re.search('\\b(before t0|prior to t0|preexisting)\\b',query_focus):
                facets_ok=facets_ok and any(f['usage']=='anchor_valid' or
                    f.get('public_marker',{}).get('status')=='general_marker' for f in packet['facts'])
            matched=matched and facets_ok
            packet['search']['request_satisfied']=matched if query else None
            packet['search']['scope']='lexical_coverage, not a narrative verdict'
            packet['search']['targets']=[s['name'] for s in focused][:5]
            packet['search']['diagnostic']=('target_found' if matched else
                'partial_excerpts' if packet['facts'] else 'no_result') if query else 'automatic_selection'
            self._size(packet)
            # Relevance metadata also count toward the documentary ceiling.
            while packet['facts'] and packet['search']['characters']>BUDGETS[role]:
                packet['facts'].pop();self._size(packet)
                # The target must not remain announced as satisfied after a proof is removed.
                packet['search']['request_satisfied']=False if query else None
                packet['search']['diagnostic']='partial_excerpts' if query else 'automatic_selection'
                self._size(packet)
            return packet

    @staticmethod
    def _size(packet):
        # The size includes the envelope, the sources and this measure itself.
        for _ in range(4):
            size=len(check_json(packet))
            if packet['search']['characters']==size:break
            packet['search']['characters']=size


def retain_context(packet, previous, role='narrator'):
    """Keep the already useful references within the same budget, new targets first."""
    seen={f['reference'] for f in packet['facts']}
    for old in previous:
        if old.get('revision')!=packet['revision']:continue
        for fact in old.get('facts',[]):
            if fact['reference'] in seen or len(packet['facts'])>=LIMITS[role]:continue
            packet['facts'].append(fact);CanonSelector._size(packet)
            if packet['search']['characters']>BUDGETS[role]:packet['facts'].pop()
            else:seen.add(fact['reference'])
    CanonSelector._size(packet)
    return packet


def select_for_turn(state,config,action,scene,query='',projects=(),role='narrator',previous=()):
    if not config.get('canon_selection',{}).get('enabled'):return None
    packet=CanonSelector(state,config).select(action,scene,state['positions'].get('player',''),query,
        projects,role,preparation_text(config))
    return retain_context(packet,previous,role)
