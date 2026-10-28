"""Private diagnostic of the context actually sent, with no new authorized source."""
from copy import deepcopy
from contextlib import closing
import json
from pathlib import Path
import sqlite3


def read(path, request_id=None):
    """Read a diagnostic that is already recorded, without preparing a turn or creating a database."""
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True)) as con:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='narrative_attempts'").fetchone():
            return {'status':'absent', 'request_id':request_id, 'diagnostic':None}
        row = con.execute('SELECT request_id,metrics FROM narrative_attempts '+
            ('WHERE request_id=?' if request_id else 'ORDER BY rowid DESC LIMIT 1'),
            (request_id,) if request_id else ()).fetchone()
    diagnostic = json.loads(row[1]).get('context_diagnostic') if row else None
    return {'status':'available' if diagnostic else 'absent',
            'request_id':row[0] if row else request_id, 'diagnostic':diagnostic}


def build(packet, selection=None, npc=None, omitted=(), omitted_entities=None):
    evidence = {ref for fact in packet.get('extra_facts', [])
                for ref in fact.get('references', []) if fact.get('text')}
    evidence.update('exchange:'+str(e['seq']) for key in ('recent_exchanges','recalled_exchanges')
                    for e in packet.get(key, []) if e['role']=='narrator' and e.get('content'))
    sources = deepcopy((selection or {}).get('sources', []))
    for source in sources:
        source['text_sent'] = source['reference'] in evidence
        if source['status']=='kept' and not source['text_sent']:
            source['final_reason'] = 'proof_absent_from_prompt'
    npc_diagnostic = deepcopy(npc or {})
    transmitted_npcs = {s['entity_id'] for s in packet.get('npc_sheets', [])}
    for sheet in npc_diagnostic.get('sheets', []):
        sheet['sent'] = sheet['entity_id'] in transmitted_npcs
        if sheet['status']=='kept' and not sheet['sent']:
            sheet['final_reason'] = 'prompt_budget' if 'npc_sheets' in omitted else 'sheet_absent_from_prompt'
    return {'memory':sources, 'neighbors':deepcopy((selection or {}).get('neighbors', [])),
            'npc':npc_diagnostic, 'omitted_blocks':[{'bloc':key, 'reason':'prompt_budget'} for key in omitted],
            'omitted_entities':deepcopy(omitted_entities if omitted_entities is not None else packet.get('omitted_entities_index', {})),
            'rule_modules':list(packet.get('rule_modules', [])),
            'sent_memory_references':sorted(set(packet.get('memory_references', [])) & evidence)}
