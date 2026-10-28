"""Explicit connection of models, disabled by default."""
from copy import deepcopy
import json
import os
import subprocess

from .contracts import check, check_json


class ModelUnavailable(RuntimeError):
    pass


class DisabledAdapter:
    def proposed(self, context, task):
        raise ModelUnavailable('No model configured; no call made')


class RecordedAdapter:
    """Local responses for tests; no generation and no network."""
    def __init__(self, responses):
        self.responses = responses

    def proposed(self, context, task):
        return check('proposal', deepcopy(self.responses[task['task_id']]))


class CommandAdapter:
    """Optional bridge: explicit command, JSON over stdin/stdout, no shell.

    Not enabled in the demo. The external program must apply its own
    token budget and permissions. No secret and no stderr is returned.
"""
    def __init__(self, argv):
        if not argv or not all(isinstance(x, str) for x in argv):
            raise ValueError('An explicit command (a list of strings) is expected')
        self.argv = argv

    def proposed(self, context, task):
        try:
            result = subprocess.run(self.argv, input=check_json({'context':context,'task':task}).encode('utf-8'),
                                    capture_output=True,
                                    env={**os.environ,'PYTHONIOENCODING':'utf-8','PYTHONUTF8':'1'},
                                    timeout=task['budget']['max_seconds'], check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ModelUnavailable('External program unavailable or timeout exceeded') from exc
        if result.returncode:
            raise ModelUnavailable('The external program failed')
        try:
            return check('proposal', json.loads(result.stdout.decode('utf-8')))
        except (ValueError, TypeError, UnicodeError) as exc:
            raise ModelUnavailable('Invalid structured response') from exc
