"""One text-in, text-out call to the chosen engine: Claude CLI, Codex CLI, Anthropic API or manual."""
import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request

from .adapters import ModelUnavailable
from .narration_claude import DEFAULT_CLAUDE_MODEL, HOST_PREFIXES, find_claude
from .narration_codex import find_codex
from . import proc

ENGINES = ('claude', 'codex', 'api', 'manual')
API_URL = 'https://api.anthropic.com/v1/messages'


class ManualRequired(Exception):
    """The manual engine never calls anything: the caller shows the prompt instead."""


def _run(command, prompt, timeout, env=None):
    with tempfile.TemporaryDirectory() as cwd:
        try:
            code, out, err = proc.run(command, prompt.encode('utf-8'), timeout, env=env, cwd=cwd)
        except subprocess.TimeoutExpired:
            raise ModelUnavailable(f'No answer within {timeout} seconds.')
        except OSError as error:
            raise ModelUnavailable(str(error))
    if code:
        raise ModelUnavailable(err.decode('utf-8', 'replace').strip()[-400:] or f'exit code {code}')
    return out.decode('utf-8', 'replace')


def _claude(prompt, model, timeout):
    executable = find_claude()
    if not executable:
        raise ModelUnavailable('Claude CLI not found.')
    command = [executable, '-p', '--model', model or DEFAULT_CLAUDE_MODEL, '--tools', '', '--strict-mcp-config',
               '--disable-slash-commands', '--no-session-persistence', '--setting-sources', '',
               '--output-format', 'json']
    env = {k: v for k, v in os.environ.items() if not k.startswith(HOST_PREFIXES)}
    result = json.loads(_run(command, prompt, timeout, env))
    if result.get('is_error'):
        raise ModelUnavailable(str(result.get('result'))[:400])
    return result.get('result') or ''


def _codex(prompt, model, timeout):
    executable = find_codex()
    if not executable:
        raise ModelUnavailable('Codex CLI not found.')
    command = [executable, 'exec', '--ignore-user-config', '--ephemeral', '--sandbox', 'read-only',
               '--skip-git-repo-check', '--json', '--color', 'never']
    if model:
        command += ['--model', model]
    messages = []
    for line in _run(command + ['-'], prompt, timeout).splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        item = event.get('item') or {}
        if event.get('type') == 'item.completed' and item.get('type') == 'agent_message':
            messages.append(item.get('text', ''))
    if not messages:
        raise ModelUnavailable('Codex returned no message.')
    return messages[-1]


def _api(prompt, model, timeout):
    key = os.environ.get('ANTHROPIC_API_KEY')
    if not key:
        raise ModelUnavailable('ANTHROPIC_API_KEY is not set.')
    body = json.dumps({'model': model or DEFAULT_CLAUDE_MODEL, 'max_tokens': 16000,
                       'messages': [{'role': 'user', 'content': prompt}]}).encode('utf-8')
    request = urllib.request.Request(API_URL, body, {'x-api-key': key, 'anthropic-version': '2023-06-01',
                                                      'content-type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise ModelUnavailable(f'API call failed: {error}')
    return ''.join(block.get('text', '') for block in data.get('content', []))


def complete(engine, prompt, model=None, timeout=900):
    if engine == 'manual':
        raise ManualRequired()
    if engine not in ENGINES:
        raise ValueError('Unknown engine: ' + str(engine))
    return {'claude': _claude, 'codex': _codex, 'api': _api}[engine](prompt, model, timeout)
