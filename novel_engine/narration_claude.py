"""Claude narrator through the Claude Code CLI, with no tools and no API key, one generation per turn.

Same contract as CodexNarrator: schema compiled locally, receipt before validation,
complete local validation. Only the command, the input and the output reading change.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from .adapters import ModelUnavailable
from .diagnostic_cli import classify, collect_failure, failure_summary, safe_detail
from .narration_codex import CodexNarrator, DEFAULT_NARRATIVE_TIMEOUT
from .planner import CodexPlanner, MARKER as PLANNER_MARKER

DEFAULT_CLAUDE_MODEL = 'claude-sonnet-5-5'
CLAUDE_MODELS = ('claude-sonnet-5-5', 'claude-opus-5-5', 'claude-haiku-5-5', 'claude-fable-5-1')
MARKERS = ('\nCURRENT TURN\n', PLANNER_MARKER)
DEFAULT_SYSTEM = 'You are the narrator of the interactive novel. Answer only with the requested JSON object.'
# Variables of a host Claude Code session (desktop app, SDK): the child CLI
# must use its own connection, not the host's private channel.
HOST_PREFIXES = ('CLAUDECODE', 'CLAUDE_CODE_', 'CLAUDE_AGENT_SDK', 'CLAUDE_PID', 'CLAUDE_EFFORT',
                 'CLAUDE_PREVIEW', 'AI_AGENT', 'BAGGAGE')


BATCH_LINE_LIMIT = 8191


def check_command_line(command, windows=None):
    """A .cmd/.bat shim (npm install) runs through cmd.exe, which refuses more than 8191 characters; the answer
    contract travels on the command line, so say so instead of failing with an unreadable system message."""
    windows = os.name == 'nt' if windows is None else windows
    if windows and str(command[0]).lower().endswith(('.cmd', '.bat')) and len(subprocess.list2cmdline(command)) > BATCH_LINE_LIMIT - 100:
        raise ModelUnavailable('The Claude CLI found is a .cmd/.bat shim and Windows limits its command line to 8191 '
                               'characters, which this answer contract exceeds. Give the native claude.exe with '
                               '--claude, or use another engine (codex, api, manual).')
    return command


def find_claude():
    """Claude Code CLI from PATH, from the native install or from the desktop app."""
    executable = shutil.which('claude.exe') or shutil.which('claude')
    if executable:
        return executable
    candidates = []
    roots = [Path.home()/'.local'/'bin']
    if os.environ.get('APPDATA'):
        roots.append(Path(os.environ['APPDATA'])/'Claude'/'claude-code')
    roots.append(Path.home()/'AppData'/'Roaming'/'Claude'/'claude-code')
    for root in roots:
        try:
            # The app sometimes keeps several versions: take the most recent one.
            for path in [root/'claude.exe', *root.glob('*/claude.exe'), *root.glob('*/*/claude.exe')]:
                if path.is_file():
                    candidates.append((path.stat().st_mtime_ns, str(path)))
        except OSError:
            continue
    return max(candidates)[1] if candidates else None


def split_prompt(prompt):
    """Stable instructions and documents as the system prompt; the turn packet as the message.

    The text is identical to the one sent to Codex, only the role of the instructions changes.
"""
    cut = max(prompt.rfind(marker) for marker in MARKERS)
    if cut <= 0:
        return DEFAULT_SYSTEM, prompt
    return prompt[:cut], prompt[cut:].lstrip('\n')


class ClaudeNarrator(CodexNarrator):
    provider = 'Claude'

    def __init__(self, model=DEFAULT_CLAUDE_MODEL, effort='low', timeout=DEFAULT_NARRATIVE_TIMEOUT,
                 executable=None, cache=False):
        super().__init__(model=model, effort=effort, timeout=timeout, executable=executable or 'claude')
        self.executable = executable or find_claude()
        self.cache = cache

    def command(self, system_path, schema_text):
        if not self.executable:
            raise ModelUnavailable('Claude CLI not found; install Claude Code or give claude.exe with --claude.')
        # No tool, MCP server, skill or kept session: a single structured answer.
        return check_command_line([self.executable, '-p', '--model', self.model, '--effort', self.effort,
                '--system-prompt-file', str(system_path), '--tools', '', '--strict-mcp-config',
                '--disable-slash-commands', '--no-session-persistence', '--setting-sources', '',
                '--output-format', 'json', '--json-schema', schema_text])

    def invocation(self, prompt, wire_schema, folder, base):
        system, message = split_prompt(prompt)
        system_path = folder/'system.txt'
        system_path.write_text(system, encoding='utf-8', newline='')
        # The CLI validator does not resolve the $schema meta-reference; the contract stays identical.
        schema_text = json.dumps({k: v for k, v in wire_schema.items() if k != '$schema'},
                                 ensure_ascii=True, separators=(',', ':'))
        # Outside the project: otherwise the CLI would read AGENTS.md and add it to the context.
        workdir = Path(tempfile.gettempdir())/'novel-engine-claude'
        workdir.mkdir(exist_ok=True)
        return self.command(system_path, schema_text), message.encode('utf-8'), str(workdir)

    def environment(self):
        env = {key: value for key, value in os.environ.items() if not key.startswith(HOST_PREFIXES)}
        if 'CLAUDECODE' in os.environ:
            # The local proxy of the host session does not accept an independent connection.
            env.pop('ANTHROPIC_BASE_URL', None)
        env.update(PYTHONIOENCODING='utf-8', PYTHONUTF8='1', DISABLE_AUTOUPDATER='1')
        if not self.cache:
            # Measured on 17 October 2026: the CLI writes the whole prompt to the cache (1.25 times the price)
            # and never rereads the system prompt on the next turn. Without cache: 20% less.
            env['DISABLE_PROMPT_CACHING'] = '1'
        return env

    def read_output(self, stdout, stderr, process):
        result = None
        for line in stdout.decode('utf-8', errors='replace').splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict) and item.get('type') == 'result':
                result = item
        if result is not None:
            self.last_metrics['usage'] = self.usage(result)
            self.last_metrics['cli_turns'] = result.get('num_turns')
            self.last_metrics['fast_mode_state'] = result.get('fast_mode_state')
        if process.returncode or result is None or result.get('is_error'):
            detail = safe_detail((result or {}).get('result') or '')
            failure = collect_failure(b'', stderr, process.returncode)
            failure['process_stopped'] = process.poll() is not None
            if detail:
                failure.update(classify(detail), detail=detail)
            if result is None and failure['category'] == 'unknown':
                failure['category'] = 'invalid_response'
            self.last_error = failure
            self.last_metrics['cli_error'] = failure
            raise ModelUnavailable(failure_summary(failure, self.provider))
        output = result.get('structured_output')
        if output is None:
            # Fallback: raw JSON text, validated afterwards like any answer.
            output = json.loads(result.get('result') or 'null')
        return json.dumps(output, ensure_ascii=False)

    @staticmethod
    def usage(result):
        """Usage in the Codex metrics format: total input, including cache read and written."""
        raw = result.get('usage') or {}
        read = raw.get('cache_read_input_tokens') or 0
        written = raw.get('cache_creation_input_tokens') or 0
        return {'input_tokens': (raw.get('input_tokens') or 0)+read+written,
                'cached_input_tokens': read, 'cache_write_input_tokens': written,
                'output_tokens': raw.get('output_tokens') or 0,
                'reasoning_output_tokens': (raw.get('output_tokens_details') or {}).get('thinking_tokens', 0),
                'speed': raw.get('speed'), 'requests': len(raw.get('iterations') or []) or 1,
                'api_cost_usd': result.get('total_cost_usd')}


class ClaudePlanner(ClaudeNarrator, CodexPlanner):
    """Periodic planner with the Claude transport and the planner schema."""
