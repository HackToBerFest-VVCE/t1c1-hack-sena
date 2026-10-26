"""
migrate_add_extraction_columns.py

One-time migration: add extraction_method and model_name to the documents
table if they are missing.  Safe to run multiple times (idempotent).

Usage:
    python backend/scripts/migrate_add_extraction_columns.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "claimlens.db"


def migrate(db_path: Path = DB_PATH) -> None:
    if not db_path.exists():
        print(f"Database not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    con = sqlite3.connect(str(db_path))
    try:
        existing_cols = {
            row[1] for row in con.execute("PRAGMA table_info(documents)").fetchall()
        }

        added: list[str] = []

        if "extraction_method" not in existing_cols:
            con.execute(
                "ALTER TABLE documents ADD COLUMN extraction_method TEXT NOT NULL DEFAULT ''"
            )
            added.append("extraction_method")

        if "model_name" not in existing_cols:
            con.execute(
                "ALTER TABLE documents ADD COLUMN model_name TEXT NOT NULL DEFAULT ''"
            )
            added.append("model_name")

        con.commit()
    finally:
        con.close()

    if added:
        print(f"Migration applied – added columns to 'documents': {', '.join(added)}")
    else:
        print("Nothing to migrate – columns already present.")


if __name__ == "__main__":
    migrate()
