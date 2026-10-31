"""Read-only preview of the arcs bridge; --apply installs the registry with a backup.

No played fact, no clock and no agenda is modified by the installation.
"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

PKG_PARENT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PKG_PARENT))
from novel_engine.arcs import ArcRegistry
from novel_engine.campaign import Campaign
from novel_engine.contracts import check_json
from novel_engine.maintenance import backup_before_write, parser
from novel_engine.narrative import narrative_lock
from novel_engine.planner import WorldPlanner
from novel_engine.scripts.migrate_planner import preserved_fingerprint


def inspect(con, plan):
    con.row_factory = sqlite3.Row
    planner = WorldPlanner(con)
    if not planner.chronicle_exists():
        raise ValueError('Explicitly install the chronicler before the arcs registry.')
    state = json.loads(con.execute('SELECT state FROM campaign').fetchone()[0])
    session = con.execute('SELECT payload FROM session WHERE id=1').fetchone()
    if session and json.loads(session[0]).get('pending_action'):
        raise ValueError('Bridge refused during a pending action: keep it and finish it first.')
    nodes = {n['id'] for n in planner.chronology_nodes()}
    ArcRegistry.verify(plan, nodes)
    minute = state['world_minute']
    registry = ArcRegistry(con)
    installed = {a['id']: a for a in registry.entries()}
    if installed and con.execute('SELECT signature FROM chronicle_arc_meta').fetchone()[0] != hashlib.sha256(check_json(plan).encode()).hexdigest():
        raise ValueError('A different registry is already installed; replacement preview refused.')
    return {'state_version': state['state_version'], 'world_minute': minute,
            'chronology': con.execute('SELECT chronology FROM chronicle_meta').fetchone()[0],
            'registry_already_installed': ArcRegistry(con).exists(),
            'arcs': [{'id': a['id'], 'title': a['title'], 'dependencies': a['dependencies'],
                     'opening_minute': installed[a['id']]['opening_minute'] if installed else minute + a['opening_days'] * 1440,
                     'end_minute': installed[a['id']]['end_minute'] if installed else minute + a['end_days'] * 1440,
                     'milestones': len(a['steps'])} for a in plan['arcs']],
            'covered_episodes': len(nodes), 'model_calls': 0}


def preview(path, plan):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as con:
        return {'status': 'preview', 'mutation': False, **inspect(con, plan)}


def install(campaign, plan):
    with narrative_lock(campaign):
        with closing(sqlite3.connect(campaign.path.resolve().as_uri() + '?mode=ro', uri=True)) as con:
            report = inspect(con, plan)
        with backup_before_write(campaign, 'arcs_registry') as backup:
            with campaign.transaction() as con:
                # New check under a transaction, never only the preview's.
                report = inspect(con, plan)
                before = preserved_fingerprint(con)
                nodes = {n['id'] for n in WorldPlanner(con).chronology_nodes()}
                added = ArcRegistry(con).install(plan, nodes, report['world_minute'])
                after = preserved_fingerprint(con)
                if before != after:
                    raise ValueError('Installation cancelled: played data or agenda modified.')
                if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Installation cancelled: incorrect integrity.')
    return {**report, 'status': 'installed_arcs', 'arcs_added': added,
            'backup': str(backup.resolve()), 'fingerprint_before': before,
            'fingerprint_after': after, 'played_data_preserved': True}


def main(argv=None):
    args_parser = parser(__doc__)
    args_parser.add_argument('--plan', required=True, help='Prospective arcs plan (JSON), chosen explicitly')
    args = args_parser.parse_args(argv)
    plan = json.loads(Path(args.plan).read_text(encoding='utf-8'))
    report = install(Campaign(args.campaign), plan) if args.apply else preview(args.campaign, plan)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
