#!/usr/bin/env python3
"""
RefSync migration: regenerate cite keys with the current scheme.

Cite keys are generated once, when a paper is added, and reused on every sync,
so libraries built before the cite-key fixes keep keys like "van Dokkum:2026"
(space), "Kereš:2005" (non-ASCII) or "Dokkum:2026" (particle dropped).

Two modes:

  default   Fix only keys that actually break LaTeX: missing, containing
            whitespace, non-ASCII, or duplicated (the later paper of a
            duplicate pair gets an a/b suffix), plus bare group keys like
            "Collaboration:2026" (-> Euclid_Collaboration:2026). Every other
            key is left alone, so existing \\cite{} commands keep working.

  --all     Bring every key to the current scheme (e.g. Dokkum:2026 ->
            van_Dokkum:2026). Keys already in the current form are never
            touched, so existing a/b suffixes don't get shuffled between papers.

Nothing is written without --apply. With --apply the script:
  * copies library.db to library.db.bak-<timestamp> first,
  * updates cite_key and the key inside each paper's stored BibTeX,
  * writes cite_key_changes-<timestamp>.csv (old_key,new_key,...) next to the
    database, so you can find/replace \\cite{} keys in existing .tex files.

Usage:
    python -m refsync.migration.regen_cite_keys            # dry run, broken keys only
    python -m refsync.migration.regen_cite_keys --all      # dry run, all outdated keys
    python -m refsync.migration.regen_cite_keys --apply    # write changes
    python -m refsync.migration.regen_cite_keys --db /path/to/library.db --all --apply

Stop the refsync server before running with --apply.
"""

import argparse
import csv
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import refsync
from refsync.models import Paper

try:
    # surname_slug only exists in the fixed bibtex module; if it's missing, the
    # `refsync` being imported is an older installed copy, not this repo.
    from refsync.services.bibtex import (
        generate_cite_key,
        surname_slug,  # noqa: F401
        update_cite_key_in_bibtex,
    )
except ImportError:
    sys.exit(
        f"The refsync package being imported ({Path(refsync.__file__).parent}) is an older "
        "install without the cite-key fixes.\nReinstall from the repo root with: pip install -e ."
    )


def _default_db_path() -> Path:
    from refsync.config import settings

    return settings.database_path


def _is_broken(key) -> bool:
    """
    True if the key would break \\cite{} (missing, whitespace, non-ASCII), or is
    a bare group key like "Collaboration:2026" that every collaboration paper
    from that year would share.
    """
    if not key or not key.isascii() or re.search(r"\s", key):
        return True
    return bool(re.match(r"(Collaboration|Team|Consortium):", key, re.IGNORECASE))


def _matches_scheme(key: str, base_key: str) -> bool:
    """Is `key` the current-scheme key for its paper (base, base+a..z, or base_<id>)?"""
    if key == base_key:
        return True
    if key.startswith(base_key):
        rest = key[len(base_key) :]
        return bool(re.fullmatch(r"[a-z]|_[A-Za-z0-9_-]+", rest))
    return False


def _load_papers(conn: sqlite3.Connection) -> list[tuple[Paper, sqlite3.Row]]:
    rows = conn.execute(
        """SELECT id, arxiv_id, bibcode, title, authors, published, added_at, cite_key, bibtex
           FROM papers
           ORDER BY published IS NULL, published, added_at, id"""
    ).fetchall()
    papers = []
    for row in rows:
        paper = Paper(
            id=row["id"],
            arxiv_id=row["arxiv_id"],
            bibcode=row["bibcode"],
            title=row["title"] or "",
            authors=json.loads(row["authors"]) if row["authors"] else [],
            published=datetime.fromisoformat(row["published"]) if row["published"] else None,
        )
        papers.append((paper, row))
    return papers


def plan_changes(papers: list[tuple[Paper, sqlite3.Row]], fix_all: bool) -> list[dict]:
    """
    Decide new keys. Papers are visited oldest-first so a/b suffixes are
    assigned in publication order for any keys that do change.
    """
    keep: set[str] = set()
    to_change: list[tuple[Paper, sqlite3.Row]] = []

    for paper, row in papers:
        key = row["cite_key"]
        if fix_all:
            ok = not _is_broken(key) and _matches_scheme(key, generate_cite_key(paper))
        else:
            ok = not _is_broken(key)
        # A key already claimed by an earlier paper is a duplicate: reassign it
        if ok and key not in keep:
            keep.add(key)
        else:
            to_change.append((paper, row))

    taken = set(keep)
    changes = []
    for paper, row in to_change:
        new_key = generate_cite_key(paper, taken)
        taken.add(new_key)
        if new_key != row["cite_key"]:
            changes.append(
                {
                    "id": paper.id,
                    "old_key": row["cite_key"] or "",
                    "new_key": new_key,
                    "title": paper.title,
                    "bibtex": row["bibtex"],
                }
            )
    return changes


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Regenerate RefSync cite keys.")
    parser.add_argument("--db", type=Path, help="Path to library.db (default: configured data dir)")
    parser.add_argument(
        "--all", action="store_true", help="Update every outdated key, not just broken ones"
    )
    parser.add_argument("--apply", action="store_true", help="Write changes (default: dry run)")
    args = parser.parse_args(argv)

    db_path = args.db or _default_db_path()
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        papers = _load_papers(conn)
        changes = plan_changes(papers, fix_all=args.all)

        mode = "all outdated keys" if args.all else "broken keys only"
        print(f"{db_path}: {len(papers)} papers, {len(changes)} key changes ({mode})\n")
        if not changes:
            return 0

        width = max(len(c["old_key"]) for c in changes)
        for c in changes:
            print(f"  {c['old_key']:<{width}}  ->  {c['new_key']}")

        if not args.apply:
            print("\nDry run. Re-run with --apply to write these changes.")
            return 0

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = db_path.with_name(f"{db_path.name}.bak-{stamp}")
        shutil.copy2(db_path, backup)

        with conn:
            for c in changes:
                bibtex = c["bibtex"]
                if bibtex:
                    bibtex = update_cite_key_in_bibtex(bibtex, c["new_key"])
                conn.execute(
                    "UPDATE papers SET cite_key = ?, bibtex = ? WHERE id = ?",
                    (c["new_key"], bibtex, c["id"]),
                )

        mapping = db_path.with_name(f"cite_key_changes-{stamp}.csv")
        with open(mapping, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["old_key", "new_key", "id", "title"])
            for c in changes:
                writer.writerow([c["old_key"], c["new_key"], c["id"], c["title"]])

        print(f"\nUpdated {len(changes)} papers.")
        print(f"Backup:  {backup}")
        print(f"Mapping: {mapping}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
