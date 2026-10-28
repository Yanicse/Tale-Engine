"""Compressed archives of finished attempts, restored byte for byte.

The full context (`prepared`, `packet`, `prompt`) of a recorded turn weighs about
530 KB; it is stored compressed in `narrative_attempt_archives` (schema version 2). The
packet is derived from the prompt and is kept separately only if it differs. The
light proposal keeps everything else, including `received`, read by the recovery budget.
An unmigrated database keeps the old full storage.
"""
import json
import zlib

from .contracts import check_json

TABLE = 'narrative_attempt_archives'
CODEC = 'zlib-json-1'
TERMINAL_STATUSES = frozenset({'recorded', 'question', 'rejected'})
HEAVY_KEYS = ('prepared', 'packet', 'prompt')
MARKER = 'context_archive'
SEPARATOR = '\nCURRENT TURN\n'


def enabled(con):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone() is not None


def _packet_from_prompt(prompt):
    return json.loads(prompt.rsplit(SEPARATOR, 1)[1])


def archive(con, key, proposal_text):
    """Store the heavy context under `key` and return the serialized light proposal."""
    proposal = json.loads(proposal_text)
    heavy = {name: proposal[name] for name in HEAVY_KEYS if name in proposal}
    if not heavy or MARKER in proposal:
        return proposal_text
    payload = {'order': list(proposal), 'content': heavy}
    prompt = heavy.get('prompt')
    if 'packet' in heavy and isinstance(prompt, str) and SEPARATOR in prompt:
        try:
            derived = _packet_from_prompt(prompt) == heavy['packet']
        except ValueError:
            derived = False
        if derived:
            payload['content'] = {name: value for name, value in heavy.items() if name != 'packet'}
            payload['packet_since_prompt'] = True
    blob = zlib.compress(check_json(payload).encode('utf-8'), 9)
    light = {name: value for name, value in proposal.items() if name not in HEAVY_KEYS}
    light[MARKER] = CODEC
    light_text = check_json(light)
    # Immediate check: no archive is accepted if the restored data differs.
    if _merge(light_text, payload) != proposal_text:
        return proposal_text
    con.execute(f'INSERT OR REPLACE INTO {TABLE} VALUES (?,?,?)', (key, CODEC, blob))
    return light_text


def _merge(light_text, payload):
    light = json.loads(light_text)
    heavy = dict(payload['content'])
    if payload.get('packet_since_prompt'):
        heavy['packet'] = _packet_from_prompt(heavy['prompt'])
    return check_json({name: heavy[name] if name in heavy else light[name] for name in payload['order']})


def restore(con, key, proposal_text):
    """Full proposal identical to the original; unchanged if it is not archived."""
    if not proposal_text or MARKER not in proposal_text:
        return proposal_text
    light = json.loads(proposal_text)
    if light.get(MARKER) != CODEC or not enabled(con):
        return proposal_text
    row = con.execute(f'SELECT codec,payload FROM {TABLE} WHERE request_id=?', (key,)).fetchone()
    if row is None:
        raise ValueError(f'Attempt archive not found: {key}')
    if row[0] != CODEC:
        raise ValueError(f'Unknown archive codec: {row[0]}')
    return _merge(proposal_text, json.loads(zlib.decompress(row[1]).decode('utf-8')))


def history_key(row_id):
    return f'history:{row_id}'
