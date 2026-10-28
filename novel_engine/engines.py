"""The four ways to run a narrator and a planner: Codex CLI, Claude CLI, Anthropic API, manual."""
import os
from pathlib import Path

ENGINES = ('codex', 'claude', 'api', 'manual')
DEFAULT_CODEX_MODEL = 'gpt-6.1-sol'


def available():
    """Engines that can run right now, in order of preference; manual always can."""
    from .narration_claude import find_claude
    from .narration_codex import find_codex
    found = []
    if find_claude():
        found.append('claude')
    if find_codex():
        found.append('codex')
    if os.environ.get('ANTHROPIC_API_KEY'):
        found.append('api')
    return found + ['manual']


def default_engine(preference=None):
    """The saved preference when it still works, else the first available engine."""
    ready = available()
    return preference if preference in ready else ready[0]


def create(engine, campaign_dir, model=None, effort='low', planner_effort='medium', timeout=240,
           planner_timeout=180, codex=None, claude=None):
    """(narrator, planner, model) for an engine name."""
    if engine == 'claude':
        from .narration_claude import ClaudeNarrator, ClaudePlanner, DEFAULT_CLAUDE_MODEL
        model = model or DEFAULT_CLAUDE_MODEL
        return (ClaudeNarrator(model=model, effort=effort, timeout=timeout, executable=claude),
                ClaudePlanner(model=model, effort=planner_effort, timeout=planner_timeout, executable=claude), model)
    if engine == 'api':
        from .narration_api import ApiNarrator, ApiPlanner
        from .narration_claude import DEFAULT_CLAUDE_MODEL
        model = model or DEFAULT_CLAUDE_MODEL
        return (ApiNarrator(model=model, effort=effort, timeout=timeout),
                ApiPlanner(model=model, effort=planner_effort, timeout=planner_timeout), model)
    if engine == 'manual':
        from .narration_manual import ManualNarrator, ManualPlanner
        folder = Path(campaign_dir) / 'manual'
        return (ManualNarrator(folder, effort=effort, timeout=timeout),
                ManualPlanner(folder, effort=planner_effort, timeout=planner_timeout), 'manual')
    from .narration_codex import CodexNarrator
    from .planner import CodexPlanner
    model = model or DEFAULT_CODEX_MODEL
    return (CodexNarrator(model=model, effort=effort, timeout=timeout, executable=codex),
            CodexPlanner(model=model, effort=planner_effort, timeout=planner_timeout, executable=codex), model)
