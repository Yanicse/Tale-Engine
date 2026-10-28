"""Manual narrator: no account, no network. The engine writes the request to a file, a person (or any
chat tool) answers it, and the next resume reads the answer through the same validation as a model's.

manual/<stage>.prompt.md   what to send: instructions, the turn, and the JSON Schema of the answer
manual/<stage>.response.json   the JSON answer, saved by the user
manual/<stage>.state.json   digest of the request, so an answer is only used for the request it answers

The stage is `turn` for the narrator and `review` for the periodic planner.
"""
import hashlib
import json
import re
from pathlib import Path

from .adapters import ModelUnavailable
from .narration_claude import split_prompt
from .narration_codex import CodexNarrator
from .planner import CodexPlanner


# Measured durations inside the packet change at every preparation; they do not change the request.
VOLATILE = re.compile(r'"[a-z_]*seconds":[0-9.eE+-]+')


class ManualNarrator(CodexNarrator):
    provider = 'Manual'
    stage = 'turn'

    def __init__(self, folder, model='manual', effort='low', timeout=240, **_):
        super().__init__(model=model, effort=effort, timeout=timeout, executable='manual')
        self.folder = Path(folder)

    def paths(self):
        return {kind: self.folder / f'{self.stage}.{kind}' for kind in ('prompt.md', 'response.json', 'state.json')}

    def request_text(self, prompt, wire_schema):
        system, message = split_prompt(prompt)
        schema = json.dumps({k: v for k, v in wire_schema.items() if k != '$schema'}, indent=1, ensure_ascii=False)
        return ('# Instructions\n\n' + system.strip() + '\n\n# Request\n\n' + message.strip()
                + '\n\n# Answer format\n\nReply with ONE JSON object and nothing else, valid against this JSON Schema:\n\n'
                + '```json\n' + schema + '\n```\n')

    def call(self, prompt, wire_schema, folder, base):
        digest = hashlib.sha256(VOLATILE.sub('', prompt).encode('utf-8')).hexdigest()
        paths = self.paths()
        self.folder.mkdir(parents=True, exist_ok=True)
        try:
            known = json.loads(paths['state.json'].read_text(encoding='utf-8')).get('sha256')
        except (OSError, ValueError, AttributeError):
            known = None
        if known == digest and paths['response.json'].is_file():
            self.last_metrics['model_calls'] = 0
            return paths['response.json'].read_text(encoding='utf-8')
        paths['prompt.md'].write_text(self.request_text(prompt, wire_schema), encoding='utf-8')
        paths['state.json'].write_text(json.dumps({'sha256': digest}), encoding='utf-8')
        paths['response.json'].unlink(missing_ok=True)
        raise ModelUnavailable(
            f"Manual engine: send {paths['prompt.md']} to a model, save its JSON answer as "
            f"{paths['response.json']}, then run: novel play --resume --retry --engine manual")


class ManualPlanner(ManualNarrator, CodexPlanner):
    stage = 'review'
