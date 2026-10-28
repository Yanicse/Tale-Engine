"""Local Claude CLI for tests: no network access, translated into the fake Codex CLI.

Checks the enforced options, rebuilds the prompt (system + message) and returns
an output in the `claude -p --output-format json` format.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    role, control = sys.argv[1:3]
    args = sys.argv[3:]
    assert args[0] == '-p' and args[args.index('--tools')+1] == ''
    for flag in ('--strict-mcp-config', '--disable-slash-commands', '--no-session-persistence'):
        assert flag in args, flag
    assert 'AGENTS.md' not in {p.name for p in Path.cwd().iterdir()}
    system = Path(args[args.index('--system-prompt-file')+1]).read_text(encoding='utf-8')
    message = sys.stdin.buffer.read().decode('utf-8')
    with tempfile.TemporaryDirectory() as folder:
        schema = Path(folder)/'schema.json'
        schema.write_text(args[args.index('--json-schema')+1], encoding='utf-8')
        done = subprocess.run([sys.executable, str(ROOT/'tests/fake_codex.py'), role, control,
                               '-c', 'service_tier="standard"', '--output-schema', str(schema)],
                              input=(system+'\n'+message).encode('utf-8'), capture_output=True)
    events = [json.loads(line) for line in done.stdout.decode('utf-8').splitlines() if line.strip()]
    messages = [e['item']['text'] for e in events if e.get('type') == 'item.completed']
    usage = {'input_tokens': 10, 'cache_read_input_tokens': 5, 'cache_creation_input_tokens': 0,
             'output_tokens': 3, 'speed': 'standard'}
    if done.returncode or not messages:
        print(json.dumps({'type': 'result', 'is_error': True, 'result': 'API Error: 503 Service Unavailable',
                          'usage': usage, 'num_turns': 1}))
        return 1
    print(json.dumps({'type': 'result', 'is_error': False, 'result': '', 'num_turns': 1,
                      'structured_output': json.loads(messages[-1]), 'usage': usage,
                      'total_cost_usd': 0, 'fast_mode_state': 'off'}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
