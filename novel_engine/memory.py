"""Read-only search and contexts filtered by recipient."""
from contextlib import closing
from pathlib import Path
import re
import sqlite3
import unicodedata

from .contracts import ROOT, check


def words(text):
    text = ''.join(c for c in unicodedata.normalize('NFKD',text.casefold()) if not unicodedata.combining(c))
    return re.findall(r'[^\W_]+',text,re.UNICODE)


def memory_terms(text):
    stop = set(words(('with from for this that those like make does also again then when want will you your '
                      'our have being going ask asked without more all already are stay but after before none '
                      'can may must she they their it this these same therefore under thus only concerning '
                      'necessary provide return memories recorded context verification during why never wait '
                      'waits waiting verify checks several weeks months days minutes behind inside turns '
                      'prefer clearly announce request speak answer reply explain explains everything')))
    return {word for word in words(text) if len(word) >= 4 and word not in stop}


def memory_scores(texts, action_text, context_text):
    """Specific words count more than terms that are present everywhere."""
    action_terms = memory_terms(action_text)
    terms = action_terms | memory_terms(context_text)
    documents = [memory_terms(text) for text in texts]
    frequencies = {term:sum(term in document for document in documents) for term in terms}
    return [sum((3 if term in action_terms else 1) * (1 + 1 / frequencies[term])
                for term in terms & document) for document in documents]


def select_facts(facts, action_text, recent=6, relevant=4, context_text=''):
    """Search before limiting, taking the player's short answers into account."""
    scores = memory_scores([item['text'] for item in facts],action_text,context_text)
    recent_ids = {item['id'] for item in facts[-recent:]}
    scored = [(scores[index], index, item)
              for index, item in enumerate(facts) if item['id'] not in recent_ids]
    recalled = sorted(scored, key=lambda row: (row[0], row[1]), reverse=True)
    selected_ids = recent_ids | {item['id'] for score, _, item in recalled[:relevant] if score > 0}
    return [{'id':item['id'], 'text':item['text'], 'category':item['category'],
             'references':item['references'], 'audience':item['audience']}
            for item in facts if item['id'] in selected_ids]


def recall_exchanges(exchanges, action_text, context_text, recent_seqs, limit=6):
    """Recall recorded dialogues, including those without a durable event."""
    scores = memory_scores([item['content'] for item in exchanges],action_text,context_text)
    scored = [(scores[index], index)
              for index, item in enumerate(exchanges)
              if item['role'] == 'narrator' and item['seq'] not in recent_seqs
              and item.get('status') == 'recorded']
    selected = set()
    for score, index in sorted(scored, reverse=True)[:limit]:
        if score <= 0:
            continue
        selected.add(index)
        if index and exchanges[index - 1]['role'] == 'player':
            selected.add(index - 1)
    return [{key:item[key] for key in ('seq', 'role', 'content')}
            for index, item in enumerate(exchanges) if index in selected]


class CanonRepository:
    def __init__(self, snapshot, revision):
        self.path = Path(snapshot)
        if not self.path.is_absolute():
            self.path = ROOT / self.path
        if not self.path.is_file():
            raise ValueError('Canonical save missing')
        self.revision = revision
        with self.connection() as con:
            metadata = dict(con.execute('SELECT key,value FROM metadata'))
            if metadata.get('revision') != str(revision) or metadata.get('consolidation_canon_revision') != str(revision):
                raise ValueError('Version or consolidation incompatible with the campaign')

    def connection(self):
        con = sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA query_only=ON')
        return closing(con)

    def search(self, query, limit=8):
        terms = words(query)
        if not terms:
            return []
        if not 1 <= limit <= 30:
            raise ValueError('Search limit between 1 and 30')
        fts = ' AND '.join('"'+word+'"' for word in terms)
        with self.connection() as con:
            rows = con.execute('''SELECT c.* FROM consolidated_fts f
                JOIN consolidated_usable_claims c ON c.claim_id=f.claim_id
                WHERE consolidated_fts MATCH ? ORDER BY rank LIMIT ?''',(fts,limit)).fetchall()
            output = []
            for row in rows:
                item = dict(row)
                item['sources'] = [dict(s) for s in con.execute('SELECT volume,pdf_page,kind,quote,image FROM sources WHERE claim_id=?',(row['claim_id'],))]
                item['known_by'] = [r[0] for r in con.execute('SELECT entity_id FROM known_by WHERE claim_id=?',(row['claim_id'],))]
                output.append(item)
            return output

    def search_sources(self, query, limit=4):
        """Broadened preparation search; never injected as an established fact."""
        terms = words(query)
        if not terms or not 1 <= limit <= 30:
            return []
        source = ROOT / 'corpus' / 'library.sqlite'
        if not source.is_file():
            raise ValueError('No source corpus indexed: corpus/library.sqlite is missing (optional full-text index of the books).')
        with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as con:
            con.row_factory = sqlite3.Row
            # Provide the source lines, without giving them a canonical status.
            return [dict(row) for row in con.execute('SELECT * FROM pages_fts WHERE pages_fts MATCH ? LIMIT ?',
                    (' AND '.join('"'+word+'"' for word in terms),limit))]


class ContextBuilder:
    def __init__(self, campaign):
        self.campaign = campaign

    def build(self, recipient='narrator', query='', player_action='', limit=24):
        return self.build_from_snapshot(self.campaign.snapshot(recipient), recipient, query, player_action, limit)

    def build_from_snapshot(self, snapshot, recipient='narrator', query='', player_action='', limit=24):
        """Build the same context from an existing transactional read."""
        if not 1 <= limit <= 100:
            raise ValueError('Context limit between 1 and 100')
        state,config,journal = snapshot
        actor = recipient.removeprefix('npc:')
        terms = words(query)
        information = []
        unknowns = []
        def add(identifier, text, category, references, audience, origin='campaign'):
            if recipient not in audience:
                return
            if terms and not all(w in words(text) for w in terms):
                return
            information.append({'id':identifier,'text':text,'origin':origin,
                                'category':category,'references':references,'audience':audience})
        for event in journal:
            add(event['event_id'],event['description'],'fact',[event['event_id']],event['audience'])
        for holder,intention in state['intentions'].items():
            add('intention:'+holder,'Intention current : '+intention,'fact',
                [f"state:{state['state_version']}:/intentions/{holder}"],
                ['narrator','player' if holder=='player' else f'npc:{holder}'])
        for resource,amount in state['resources'].items():
            owner=config.get('resource_owners',{}).get(resource)
            audience=['narrator']
            if owner:
                audience.append('player' if owner=='player' else f'npc:{owner}')
            add('resource:'+resource,f'Ressource {resource} : {amount}.','fact',
                [f"state:{state['state_version']}:/resources/{resource}"],audience)
        for holder, beliefs in state['beliefs'].items():
            if recipient not in ('narrator', 'player' if holder=='player' else f'npc:{holder}'):
                continue
            for i, belief in enumerate(beliefs):
                add(f'belief:{holder}:{i}',belief['statement']+f" (belief {belief['status']})",'belief',
                    belief['references'],['narrator','player' if holder=='player' else f'npc:{holder}'])
        commitment_ids = []
        for identifier, commitment in state['commitments'].items():
            if recipient in commitment['audience']:
                commitment_ids.append(identifier)
                add('commitment:'+identifier,commitment['description']+f" ({commitment['status']})",'fact',
                    commitment['references'],commitment['audience'])
        for group in ('inventory','effects','relations','powers','projects'):
            for identifier, value in state.get(group, {}).items():
                if group=='effects' and value['expires_at'] is not None and value['expires_at'] <= state['world_minute']:
                    continue
                text = value.get('description',f"Relation {value.get('from_id')} to {value.get('to_id')}: {value.get('attitude')}")
                if group=='inventory':
                    text += f" Quantity: {value['quantity']} ({value.get('quantity_precision','exact')}); equipped: {value['equipped']}; location: {value.get('location','unknown')}."
                if group=='projects':
                    text = value['title'] + ' : ' + '; '.join(value['completed'] + value['remaining'] + value['unknowns'])
                if group=='powers':
                    text += ' Limits: '+value['limits']
                add(group+':'+identifier,text,'fact',value['references'],value['audience'])
        if query and not (recipient=='narrator' and config.get('canon_selection',{}).get('enabled')):
            repository = CanonRepository(state['versions']['canon_snapshot'],state['versions']['canon_revision'])
            for claim in repository.search(query):
                # An explicit validation is required for the starting point.
                access = config.get('canon_access',{}).get(claim['claim_id'],{})
                if not access.get('validated_for_start') or recipient not in access.get('audience',[]):
                    if recipient == 'narrator':
                        unknowns.append('Canonical candidate not validated for this period: '+claim['claim_id'])
                    continue
                if recipient != 'narrator' and claim['claim_id'] not in state['knowledge'].get(actor,[]):
                    continue
                add(claim['claim_id'],claim['statement'], 'fact' if claim['category']=='canon' else 'testimony',
                    [claim['claim_id']],access['audience'],'canon')
        if not information and query:
            unknowns.append('No accessible information found; this does not prove its absence from the sources.')
        if len(information)>limit:
            unknowns.append('Limited context; refine the search to retrieve the other relevant memories.')
        position = state['positions'].get(actor,'position not provided')
        label='Demo scene' if config.get('fictional') else 'Campaign scene'
        scene = f"{label} : {state['positions'].get('player')}" if recipient=='narrator' else f'Position : {position}'
        return check('context',{'schema_version':'0.2','context_id':f"context:{recipient}:{state['state_version']}",
                    'campaign_id':state['campaign_id'],'state_version':state['state_version'],
                    'world_minute':state['world_minute'],'versions':state['versions'],'recipient':recipient,
                    'scene':scene,'player_action':player_action if recipient=='narrator' else '',
                    'information':information[-limit:],'unknowns':unknowns,'commitment_ids':commitment_ids,
                    'rules':config.get('rule_ids',[])})
