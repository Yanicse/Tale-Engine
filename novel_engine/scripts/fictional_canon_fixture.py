"""Small, entirely invented canon to test the interfaces without reading the novels."""
import sqlite3
from contextlib import closing


def create_fixture(path):
    with closing(sqlite3.connect(path)) as con, con:
        con.executescript('''
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            INSERT INTO metadata VALUES ('revision','125'),('consolidation_canon_revision','125');
            CREATE TABLE entities(entity_id TEXT PRIMARY KEY,kind TEXT,name TEXT,identity_status TEXT);
            CREATE TABLE aliases(entity_id TEXT,name TEXT);
            CREATE TABLE canonical_entities(raw_id TEXT PRIMARY KEY,canonical_id TEXT);
            CREATE TABLE consolidated_usable_claims(claim_id TEXT PRIMARY KEY,canonical_subject_id TEXT,
                statement TEXT,predicate TEXT,world_time TEXT,category TEXT,fact_id TEXT);
            CREATE TABLE sources(claim_id TEXT,volume INTEGER,pdf_page INTEGER);
            CREATE VIRTUAL TABLE consolidated_fts USING fts5(claim_id UNINDEXED,entity_name,aliases,statement,predicate);
            INSERT INTO entities VALUES ('town','place','Bright Town','named'),
                                        ('guild','faction','Local Guild','named');
            INSERT INTO aliases VALUES ('town','Bright Town'),('guild','Local Guild');
            INSERT INTO canonical_entities VALUES ('town','town'),('guild','guild');
        ''')
        records=[
            ('v05_p0001_0004:c1','guild','The local Guild welcomes adventurers in the center of Bright Town.','sheet_function'),
            ('v05_p0001_0004:c2','guild',"The local Guild uses three fictional ranks to organize the adventurers' requests.",'rank_hierarchy'),
            ('v05_p0001_0004:c3','town','Bright Town has a square where visitors can meet the craftsmen.','geography'),
            ('v05_p0001_0004:c4','guild','A messenger proposed a meeting to the local Guild in a fictional scene of the story.','meeting_proposal')]
        for ref,subject,text,predicate in records:
            con.execute('INSERT INTO consolidated_usable_claims VALUES (?,?,?,?,?,?,?)',
                        (ref,subject,text,predicate,None,'canon',ref))
            con.execute('INSERT INTO sources VALUES (?,5,1)',(ref,))
            name='Local Guild' if subject=='guild' else 'Bright Town'
            con.execute('INSERT INTO consolidated_fts VALUES (?,?,?,?,?)',(ref,name,name,text,predicate))
    return path
