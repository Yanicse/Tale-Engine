"""Install the planner and its historical excerpts, with no remote call."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
import uuid

PKG_PARENT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PKG_PARENT))
from novel_engine.indexed_memory import MemoryIndex
from novel_engine.narrative import narrative_lock
from novel_engine.planner import WorldPlanner
from novel_engine.scripts.migrate_memory import game_fingerprint


def preserved_fingerprint(con):
    digest=hashlib.sha256(game_fingerprint(con).encode())
    for table in ('world_agenda','agenda_history','agenda_receipts','agenda_continuations','agenda_meta'):
        if con.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():
            for row in con.execute('SELECT * FROM '+table+' ORDER BY rowid'):
                digest.update(json.dumps(list(row),ensure_ascii=False,separators=(',',':')).encode('utf-8'))
    return digest.hexdigest()


def migrate(campaign):
    started=time.monotonic()
    with narrative_lock(campaign):
        with closing(sqlite3.connect(campaign.path)) as source:
            before=preserved_fingerprint(source)
            folder=campaign.path.parent/'saves';folder.mkdir(exist_ok=True)
            backup=folder/('before_planner'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]+'.sqlite')
            with closing(sqlite3.connect(backup)) as target:source.backup(target)
        with campaign.transaction() as con:
            campaign._session(con)
            MemoryIndex(con).sync()
            planner=WorldPlanner(con);planner.initialize()
            state=campaign._state(con)
            planner.seed_archive()
            after=preserved_fingerprint(con)
            if after!=before:raise ValueError('Migration cancelled: state, exchanges or agenda modified.')
            integrity=con.execute('PRAGMA integrity_check').fetchone()[0]
            if integrity!='ok':raise ValueError('Migration cancelled: incorrect integrity.')
            summaries=con.execute('SELECT COUNT(*) FROM planner_summaries').fetchone()[0]
        return {'status':'planner_installed','backup':str(backup.resolve()),
                'fingerprint_before':before,'fingerprint_after':after,'game_data_and_agenda_preserved':True,
                'integrity':integrity,'state_version':state['state_version'],'world_minute':state['world_minute'],
                'extractive_periods':summaries,'model_calls':0,
                'seconds':round(time.monotonic()-started,3)}


def main(argv=None):
    from novel_engine.maintenance import execute, parser as maintenance_parser
    parser = maintenance_parser(__doc__)
    parser.add_argument('--report')
    args = parser.parse_args(argv)
    report = execute(args, 'migrate_planner', migrate)
    if args.apply and args.report:
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
