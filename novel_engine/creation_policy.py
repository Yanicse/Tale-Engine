"""Common policy, independent of the transport and of the installed mechanics."""
from .prompts import load as load_prompt
INSTRUCTIONS = load_prompt('creation_policy.instructions')

TRANSPORT = load_prompt('creation_policy.transport')
