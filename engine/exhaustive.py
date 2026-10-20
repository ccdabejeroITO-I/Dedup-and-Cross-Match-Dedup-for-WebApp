"""Uncapped, streamed pair discovery and a separate pair-level audit export."""
from pathlib import Path
from contextlib import closing
import sqlite3


def build_candidates(conn, cross, options, log):
    from engine import crossmatch_engine as e
    right = 'rows_b' if cross else 'rows_a'
    relation = '1=1' if cross else 'a.row_id < b.row_id'
    terms = [('b1',2),('b2',2),('b3',2),('b4',4),('b5',4),('b8',3),('b9',3),
             ('b10',3),('b11',3),('b12',3)] + [(f'b{i}',2) for i in range(13,21)]
    evidence = '+'.join(f'CASE WHEN a.{c}<>0 AND a.{c}=b.{c} THEN {w} ELSE 0 END' for c,w in terms)
    hits = '+'.join(f'CASE WHEN a.{c}<>0 AND a.{c}=b.{c} THEN 1 ELSE 0 END' for c,_ in terms)
    # A view avoids storing the quadratic Cartesian product. Only scored matches
    # are retained on disk. No name block, top-N, or exact-row exclusion applies.
    conn.execute(f'''CREATE VIEW fuzzy_candidates AS
        SELECT a.row_id a,b.row_id b,({evidence}) evidence,({hits}) hits,
        CASE WHEN a.birth<>'' AND a.birth=b.birth THEN 1 ELSE 0 END exact_birth,
        CASE WHEN a.byear>0 AND b.byear>0 THEN ABS(a.byear-b.byear) ELSE 9999 END year_gap
        FROM rows_a a JOIN {right} b ON {relation}
        WHERE (a.finding & {e.F_DEDUP_BLOCKING_FINDING})=0
        AND (b.finding & {e.F_DEDUP_BLOCKING_FINDING})=0''')
    conn.execute('CREATE TEMP TABLE fuzzy_pre_review(a INTEGER PRIMARY KEY,b INTEGER)')
    conn.commit()
    conn.execute('PRAGMA locking_mode=NORMAL')
    conn.commit()
    log('Exhaustive fuzzy search: every eligible pair, including rows with exact matches; no candidate caps. Cost grows quadratically.')


def pair_path(db_path):
    return Path(db_path).with_name('all_match_pairs.sqlite')


def capture(results, db_path):
    with closing(sqlite3.connect(pair_path(db_path))) as pairs:
        pairs.execute('CREATE TABLE IF NOT EXISTS pairs(a INTEGER,b INTEGER,type INTEGER,score REAL,PRIMARY KEY(a,b)) WITHOUT ROWID')
        for result in results:
            a,b,ok,kind,score,*_ = result
            if ok:
                pairs.execute('INSERT OR REPLACE INTO pairs VALUES (?,?,?,?)', (a,b,kind,score))
            yield result
        pairs.commit()


def export_pairs(workbook, stage_a, stage_b, options):
    from engine import crossmatch_engine as e
    right = 'rows_b' if stage_b else 'rows_a'
    relation = '1=1' if stage_b else 'a.row_id<b.row_id'
    count = 0
    with closing(sqlite3.connect(pair_path(stage_a.db_path))) as pairs:
        pairs.execute('CREATE TABLE IF NOT EXISTS pairs(a INTEGER,b INTEGER,type INTEGER,score REAL,PRIMARY KEY(a,b)) WITHOUT ROWID')
        pairs.execute('ATTACH DATABASE ? AS source', (str(stage_a.db_path),))
        # Verify complete keys, not hashes; include ALL exact references, even
        # pairs the fuzzy scorer rejects because the mapping is ID-only.
        for key,kind in ([('swap_key',e.TYPE_TRUE_SWAP)] if options.enable_swap else []) + [('final_key',e.TYPE_TRUE)]:
            pairs.execute(f'''INSERT OR REPLACE INTO pairs
                SELECT a.row_id,b.row_id,?,100 FROM source.rows_a a JOIN source.{right} b
                ON a.{key}=b.{key} AND a.{key}<>'' AND {relation}
                WHERE (a.finding & ?)=0 AND (b.finding & ?)=0''',
                (kind,e.F_DEDUP_BLOCKING_FINDING,e.F_DEDUP_BLOCKING_FINDING))
        headers = ['A Record','Reference File','Reference Record','Pair Decision','Similarity',
                   'A Normalized Name','Reference Normalized Name','A Birthdate','Reference Birthdate',
                   'Interpretation']
        sheet = None
        for a,b,kind,score,an,bn,ab,bb in pairs.execute(f'''SELECT p.a,p.b,p.type,p.score,a.name,b.name,a.birth,b.birth
            FROM pairs p JOIN source.rows_a a ON a.row_id=p.a
            JOIN source.{right} b ON b.row_id=p.b ORDER BY p.a,p.score DESC,p.b'''):
            if count % e.EXCEL_MAX_DATA_ROWS == 0:
                part = count // e.EXCEL_MAX_DATA_ROWS + 1
                sheet = workbook.add_worksheet('ALL MATCH PAIRS' + (f' {part}' if part>1 else ''))
                sheet.write_row(0,0,headers)
                sheet.freeze_panes(1,0)
            values = [a+1,'B' if stage_b else 'A',b+1,
                      'REVIEW CANDIDATE' if kind==e.TYPE_FINDING else e.TYPE_LABELS[kind],score,
                      an,bn,ab,bb,'Pair evidence only; see primary categories and conditions before acting']
            sheet.write_row(count % e.EXCEL_MAX_DATA_ROWS + 1,0,[e._safe_excel_value(v) for v in values])
            count += 1
        if sheet is None:
            workbook.add_worksheet('ALL MATCH PAIRS').write_row(0,0,headers)
        pairs.commit()
    return count
