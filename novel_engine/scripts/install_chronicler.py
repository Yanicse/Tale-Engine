"""Install the chronicler and its initial clocks, with no remote call.

Read-only preview by default; --apply backs up the campaign before adding the tables.
"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import time
import uuid

PKG_PARENT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PKG_PARENT))
from novel_engine.narrative import narrative_lock
from novel_engine.planner import WorldPlanner
from novel_engine.scripts.migrate_planner import preserved_fingerprint

CHRONOLOGY='canon/chronology.json'


def install(campaign,clocks,chronology=CHRONOLOGY):
    started=time.monotonic()
    with narrative_lock(campaign):
        with closing(sqlite3.connect(campaign.path)) as source:
            before=preserved_fingerprint(source)
            folder=campaign.path.parent/'saves';folder.mkdir(exist_ok=True)
            backup=folder/('before_chronicler'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]+'.sqlite')
            with closing(sqlite3.connect(backup)) as target:source.backup(target)
        with campaign.transaction() as con:
            planner=WorldPlanner(con)
            if not planner.exists():raise ValueError('Planner missing: install migrate_planner first.')
            added=planner.install_chronicle(clocks,chronology)
            after=preserved_fingerprint(con)
            if after!=before:raise ValueError('Installation cancelled: state, exchanges or agenda modified.')
            integrity=con.execute('PRAGMA integrity_check').fetchone()[0]
            if integrity!='ok':raise ValueError('Installation cancelled: incorrect integrity.')
            active=len(planner.clocks())
    return {'status':'chronicler_installed','backup':str(backup.resolve()),'added_clocks':added,
            'active_clocks':active,'game_data_and_agenda_preserved':True,'integrity':integrity,
            'model_calls':0,'seconds':round(time.monotonic()-started,3)}


def main(argv=None):
    from novel_engine.maintenance import execute, parser as maintenance_parser
    parser=maintenance_parser(__doc__)
    parser.add_argument('--clocks',required=True,help='JSON file of the initial clocks')
    parser.add_argument('--chronology',default=CHRONOLOGY)
    args=parser.parse_args(argv)
    clocks=json.loads(Path(args.clocks).read_text(encoding='utf-8'))
    report=execute(args,'install_chronicler',lambda campaign:install(campaign,clocks,args.chronology))
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
