"""Narrator through the Anthropic Messages API: one forced tool call per turn, standard library only.

Same contract as the CLI narrators: the schema is compiled locally, the answer is saved as a receipt
before validation, and the complete local validation runs afterwards. Only the transport changes.
The key comes from ANTHROPIC_API_KEY and is never written to a file or a receipt.
"""
import json
import os
import urllib.error
import urllib.request

from .adapters import ModelUnavailable
from .narration_claude import DEFAULT_CLAUDE_MODEL, split_prompt
from .narration_codex import CodexNarrator, DEFAULT_NARRATIVE_TIMEOUT
from .planner import CodexPlanner

API_URL = 'https://api.anthropic.com/v1/messages'
API_VERSION = '2023-06-01'
MAX_OUTPUT_TOKENS = 16000
TOOL = 'answer'


class ApiNarrator(CodexNarrator):
    provider = 'Anthropic API'
    url = API_URL

    def __init__(self, model=DEFAULT_CLAUDE_MODEL, effort='low', timeout=DEFAULT_NARRATIVE_TIMEOUT, api_key=None):
        super().__init__(model=model, effort=effort, timeout=timeout, executable='api')
        self.api_key = api_key or os.environ.get('ANTHROPIC_API_KEY')

    def request_body(self, prompt, wire_schema):
        system, message = split_prompt(prompt)
        schema = {key: value for key, value in wire_schema.items() if key != '$schema'}
        return {'model': self.model, 'max_tokens': MAX_OUTPUT_TOKENS, 'system': system,
                'messages': [{'role': 'user', 'content': message}],
                'tools': [{'name': TOOL, 'description': 'Return the structured result of the turn.', 'input_schema': schema}],
                'tool_choice': {'type': 'tool', 'name': TOOL}}

    def call(self, prompt, wire_schema, folder, base):
        if not self.api_key:
            raise ModelUnavailable('ANTHROPIC_API_KEY is not set.')
        body = json.dumps(self.request_body(prompt, wire_schema), ensure_ascii=False).encode('utf-8')
        request = urllib.request.Request(self.url, body, {'x-api-key': self.api_key, 'anthropic-version': API_VERSION,
                                                          'content-type': 'application/json'})
        self.last_metrics['model_calls'] = 1
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode('utf-8', 'replace')[:300]
            self.last_error = {'category': 'http_%d' % error.code, 'detail': detail}
            raise ModelUnavailable('Anthropic API error %d: %s' % (error.code, detail))
        except (urllib.error.URLError, OSError, ValueError) as error:
            self.last_error = {'category': 'network', 'detail': str(error)[:300]}
            raise ModelUnavailable('Anthropic API call failed: %s' % error)
        usage = data.get('usage') or {}
        self.last_metrics['usage'] = {'input_tokens': usage.get('input_tokens', 0),
                                      'output_tokens': usage.get('output_tokens', 0)}
        if data.get('stop_reason') == 'max_tokens':
            raise ModelUnavailable('Anthropic API answer cut at the output limit; action kept.')
        for block in data.get('content') or []:
            if block.get('type') == 'tool_use' and block.get('name') == TOOL:
                return json.dumps(block.get('input'), ensure_ascii=False)
        raise ModelUnavailable('Anthropic API answered without the structured result; action kept.')


class ApiPlanner(ApiNarrator, CodexPlanner):
    """Periodic planner with the API transport and the planner schema."""
