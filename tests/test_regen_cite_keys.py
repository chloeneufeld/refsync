import csv
import json
import sqlite3

from refsync.migration import regen_cite_keys as regen

SCHEMA = """CREATE TABLE papers (id TEXT PRIMARY KEY, arxiv_id TEXT, bibcode TEXT, title TEXT,
    authors TEXT, published TEXT, added_at TEXT, cite_key TEXT, bibtex TEXT)"""

ROWS = [
    # id, author, published, current key
    ("p1", "Pieter van Dokkum", "2026-01-01", "van Dokkum:2026"),  # space: broken
    ("p2", "Dušan Kereš", "2005-01-01", "Kereš:2005"),  # non-ASCII: broken
    ("p3", "Arjen van der Wel", "2014-01-01", "Wel:2014"),  # valid but outdated
    ("p4", "John Smith", "2024-01-01", "Smith:2024"),  # fine
    ("p5", "Jane Smith", "2024-06-01", "Smith:2024a"),  # fine, suffix must not move
    ("p6", "Bob Smith", "2024-09-01", "Smith:2024"),  # duplicate of p4
    ("p7", "Euclid Collaboration", "2026-02-01", "Collaboration:2026"),  # bare group key
    ("p8", "DESI Collaboration", "2026-03-01", "Collaboration:2026"),  # bare + duplicate
]


def _make_db(tmp_path):
    db = tmp_path / "library.db"
    conn = sqlite3.connect(db)
    conn.execute(SCHEMA)
    for pid, author, pub, key in ROWS:
        conn.execute(
            "INSERT INTO papers VALUES (?,?,?,?,?,?,?,?,?)",
            (
                pid,
                None,
                None,
                pid,
                json.dumps([author]),
                pub,
                pub,
                key,
                f"@ARTICLE{{{key},\n  title = {{T}}\n}}",
            ),
        )
    conn.commit()
    conn.close()
    return db


def _keys(db):
    conn = sqlite3.connect(db)
    out = dict(conn.execute("SELECT id, cite_key FROM papers").fetchall())
    bib = dict(conn.execute("SELECT id, bibtex FROM papers").fetchall())
    conn.close()
    return out, bib


def test_dry_run_writes_nothing(tmp_path):
    db = _make_db(tmp_path)
    before = _keys(db)
    assert regen.main(["--db", str(db)]) == 0
    assert _keys(db) == before


def test_default_fixes_only_broken_and_duplicates(tmp_path):
    db = _make_db(tmp_path)
    assert regen.main(["--db", str(db), "--apply"]) == 0
    keys, bib = _keys(db)
    assert keys == {
        "p1": "van_Dokkum:2026",
        "p2": "Keres:2005",
        "p3": "Wel:2014",  # untouched in default mode
        "p4": "Smith:2024",
        "p5": "Smith:2024a",
        "p6": "Smith:2024b",
        "p7": "Euclid_Collaboration:2026",
        "p8": "DESI_Collaboration:2026",
    }
    assert bib["p1"].startswith("@ARTICLE{van_Dokkum:2026,")
    assert list(tmp_path.glob("library.db.bak-*"))
    rows = list(csv.DictReader(open(next(tmp_path.glob("cite_key_changes-*.csv")))))
    assert {(r["old_key"], r["new_key"]) for r in rows} == {
        ("van Dokkum:2026", "van_Dokkum:2026"),
        ("Kereš:2005", "Keres:2005"),
        ("Smith:2024", "Smith:2024b"),
        ("Collaboration:2026", "Euclid_Collaboration:2026"),
        ("Collaboration:2026", "DESI_Collaboration:2026"),
    }


def test_all_mode_updates_outdated_but_keeps_suffixes(tmp_path):
    db = _make_db(tmp_path)
    assert regen.main(["--db", str(db), "--all", "--apply"]) == 0
    keys, _ = _keys(db)
    assert keys["p3"] == "van_der_Wel:2014"
    assert (keys["p4"], keys["p5"], keys["p6"]) == ("Smith:2024", "Smith:2024a", "Smith:2024b")
