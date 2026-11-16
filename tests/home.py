"""A throwaway home for the tests that create packs and campaigns.

`isolated_home(case)` points the engine at a temporary folder for the length of a test, or of a whole class when it
is called from `setUpClass`. Nothing is then written to, or removed from, the real `novels/` and `campaigns/` folders,
so running the suite can never touch a campaign of the person who runs it.
"""
import importlib
import inspect
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPOSITORY = Path(__file__).resolve().parents[1]
FOLDERS = ('novels', 'campaigns')
# Modules that keep the home, or a path inside it, in a module constant or in a default argument.
MODULES = ('cli', 'resume', 'world_setup', 'campaign_tools', 'scripts.read_game', 'scripts.play_narrative',
           'scripts.routine')


def _moved(value, real, home):
    """The same path inside the temporary home, or None when the value is not a path of the real home's folders."""
    if not isinstance(value, Path):
        return None
    for folder in FOLDERS:
        if value == real / folder or real / folder in value.parents:
            return home / value.relative_to(real)
    return None


def isolated_home(case):
    """Create the temporary home, redirect the engine to it and undo everything when `case` is done.

    `case` is a test (`self`) or a test class (`cls` in `setUpClass`). Returns the home, which already holds
    empty `novels` and `campaigns` folders."""
    from novel_engine import contracts
    for name in MODULES:
        importlib.import_module('novel_engine.' + name)
    real = contracts.ROOT
    home = Path(tempfile.mkdtemp(prefix='.tmp-home-', dir=REPOSITORY)).resolve()
    for folder in FOLDERS:
        (home / folder).mkdir()
    patches = []
    for name, module in list(sys.modules.items()):
        if module is None or not (name == 'novel_engine' or name.startswith('novel_engine.')):
            continue
        for attribute, value in list(vars(module).items()):
            if attribute == 'ROOT' and value == real:
                patches.append(mock.patch.object(module, 'ROOT', home))
            elif _moved(value, real, home) is not None:
                patches.append(mock.patch.object(module, attribute, _moved(value, real, home)))
            elif inspect.isfunction(value) and value.__module__ == name and value.__defaults__:
                defaults = tuple(_moved(default, real, home) or default for default in value.__defaults__)
                if defaults != value.__defaults__:
                    patches.append(mock.patch.object(value, '__defaults__', defaults))
    cleanup = case.addClassCleanup if isinstance(case, type) else case.addCleanup
    cleanup(shutil.rmtree, home, True)
    for patch in patches:
        patch.start()
        cleanup(patch.stop)
    return home
