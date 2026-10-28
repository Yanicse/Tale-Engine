"""World files of a pack and their installation in a new campaign.

Every new campaign gets the periodic planner (summaries of the story). A pack may also ship:

  chronology.json  {"nodes": [{"id", "title", ...}], "edges": [{"before", "after"}]}: the episodes clocks and arcs cite
  clocks.json      [clock, ...]: the secret clocks of the factions (the chronicler)
  arcs.json        {"version": 1, "arcs": [...], "outside_arcs": {}}: the prospective arcs (needs chronology.json)

Having one of them installs the chronicler; arcs.json also installs the arcs registry. `novel validate` runs the same
installation on an empty in-memory database, so a mistake is reported with its file before any campaign exists.
"""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .arcs import ArcRegistry
from .contracts import ROOT
from .planner import WorldPlanner

FILES = ('chronology.json', 'clocks.json', 'arcs.json')
EMPTY_CHRONOLOGY = {'nodes': [], 'edges': []}


def present(pack_dir):
    return any((Path(pack_dir) / name).is_file() for name in FILES)


def load(pack_dir):
    """(chronology, clocks, arcs plan or None); ValueError names the file that is wrong."""
    pack_dir = Path(pack_dir)

    def read(name, kind, default):
        path = pack_dir / name
        if not path.is_file():
            return default
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except ValueError as error:
            raise ValueError(f'{name}: not valid JSON ({error})')
        if not isinstance(data, kind):
            raise ValueError(f'{name}: must contain a JSON {"array" if kind is list else "object"}')
        return data

    chronology = read('chronology.json', dict, dict(EMPTY_CHRONOLOGY))
    nodes = chronology.get('nodes')
    if not isinstance(nodes, list) or not all(isinstance(n, dict) and isinstance(n.get('id'), str)
                                              and isinstance(n.get('title'), str) for n in nodes):
        raise ValueError('chronology.json: "nodes" must list objects with a text "id" and "title"')
    if len({n['id'] for n in nodes}) != len(nodes):
        raise ValueError('chronology.json: duplicate node id')
    clocks = read('clocks.json', list, [])
    plan = read('arcs.json', dict, None)
    if plan is not None and not nodes:
        raise ValueError('arcs.json: arcs cite chronology.json episodes, which is missing or empty')
    return chronology, clocks, plan


def install(con, pack_dir, chronology_path, minute=0):
    """Planner always; chronicler and arcs when the pack ships their files. `chronology_path` is relative to ROOT
    and already holds the campaign's copy of the chronology."""
    planner = WorldPlanner(con)
    planner.initialize()
    if not present(pack_dir):
        return {'planner': True, 'chronicler': False, 'arcs': 0}
    _, clocks, plan = load(pack_dir)
    added = planner.install_chronicle(clocks, chronology_path)
    arcs = 0
    if plan is not None:
        arcs = ArcRegistry(con).install(plan, {n['id'] for n in planner.chronology_nodes()}, minute)
    return {'planner': True, 'chronicler': True, 'clocks': added, 'arcs': arcs}


def install_into_campaign(campaign, pack_dir):
    """Install inside the campaign's own transaction; the chronology is copied next to the save so that later
    edits of the pack never change a running campaign."""
    pack_dir = Path(pack_dir)
    target = campaign.path.parent / 'chronology.json'
    chronology = load(pack_dir)[0] if present(pack_dir) else None
    if chronology is not None:
        target.write_text(json.dumps(chronology, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    try:
        with campaign.transaction() as con:
            minute = json.loads(con.execute('SELECT state FROM campaign WHERE id=1').fetchone()[0])['world_minute']
            return install(con, pack_dir, target.relative_to(ROOT).as_posix(), minute)
    except Exception:
        target.unlink(missing_ok=True)
        raise


class _DryPlanner(WorldPlanner):
    """Planner whose chronology comes from memory, so a pack is checked wherever it lives."""

    def __init__(self, con, nodes):
        super().__init__(con)
        self.nodes = nodes

    def chronology_nodes(self):
        return [{'id': n['id'], 'phase': None, 'title': n['title'], 'time_marker': None, 'date_source': None,
                 'proofs': [], 'after': []} for n in self.nodes]


def check(pack_dir):
    """Problems of the pack's world files, found by installing them in an empty database."""
    if not present(pack_dir):
        return []
    try:
        chronology, clocks, plan = load(pack_dir)
    except ValueError as error:
        return [str(error)]
    with closing(sqlite3.connect(':memory:')) as con:
        con.row_factory = sqlite3.Row
        planner = _DryPlanner(con, chronology['nodes'])
        try:
            planner.install_chronicle(clocks, 'chronology.json')
        except (ValueError, KeyError, TypeError) as error:
            return [f'clocks.json: {error}']
        if plan is not None:
            try:
                ArcRegistry(con).install(plan, {n['id'] for n in chronology['nodes']}, 0)
            except (ValueError, KeyError, TypeError) as error:
                return [f'arcs.json: {error}']
    return []
