"""Additive migration with SQLite backup and check of the played state."""
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
from novel_engine.campaign import Campaign
from novel_engine.indexed_memory import MemoryIndex
from novel_engine.narrative import narrative_lock


def game_fingerprint(con):
    digest=hashlib.sha256()
    for table in ('campaign','requests','events','session','exchanges','narrative_attempts','cycles','tasks'):
        if con.execute('SELECT 1 FROM sqlite_master WHERE name=?',(table,)).fetchone():
            for row in con.execute('SELECT * FROM '+table+' ORDER BY rowid'):
                digest.update(json.dumps(list(row),ensure_ascii=False,separators=(',',':')).encode('utf-8'))
    return digest.hexdigest()


def migrate(campaign,rebuild=False):
    with narrative_lock(campaign):
        with closing(sqlite3.connect(campaign.path)) as source:
            before=game_fingerprint(source)
            folder=campaign.path.parent/'saves'
            folder.mkdir(exist_ok=True)
            backup=folder/('before_memory'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]+'.sqlite')
            with closing(sqlite3.connect(backup)) as target:
                source.backup(target)
        with campaign.transaction() as con:
            campaign._session(con)
            index=MemoryIndex(con)
            delta=index.rebuild() if rebuild else index.sync()
            after=game_fingerprint(con)
            if before!=after:
                raise ValueError('The game data changed; migration cancelled.')
            if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ValueError('Incorrect SQLite integrity; migration cancelled.')
            con.execute("INSERT INTO memory_fts(memory_fts,rank) VALUES ('integrity-check',1)")
            count=con.execute('SELECT COUNT(*) FROM memory_sources').fetchone()[0]
        return {'status':'indexed_memory','backup':str(backup.resolve()),'sources':count,
                'game_data_preserved':True,'game_fingerprint':after,'delta':delta}


def main(argv=None):
    from novel_engine.maintenance import execute, parser as maintenance_parser
    parser = maintenance_parser(__doc__)
    parser.add_argument('--rebuild', action='store_true', help='Rebuild the projections from the archives')
    args = parser.parse_args(argv)
    report = execute(args, 'migrate_memory', lambda campaign: migrate(campaign, rebuild=args.rebuild))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
