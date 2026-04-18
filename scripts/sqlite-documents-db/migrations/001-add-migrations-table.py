#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "boto3>=1.35",
# ]
# ///
"""
Migration 001: Create the migrations tracking table.

Creates the `migrations` table and seeds it with entries for 000 and 001 so
subsequent runs know both of those migrations have already been applied.

Usage (local dev — point at a copy of the DB):
    SQLITE_DB_PATH=/tmp/my-local-copy.db uv run scripts/sqlite-documents-db/migrations/001-add-migrations-table.py

Usage (against real S3 DB):
    AWS_PROFILE=just-my-links APPLICATION_BUCKET=just-my-links-dev \\
        uv run scripts/sqlite-documents-db/migrations/001-add-migrations-table.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from framework import db_connection, if_not_applied  # noqa: E402

with db_connection() as conn:
    with if_not_applied(conn, __file__) as run:
        if run:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS migrations (
                    name       TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            # Seed 000 as already applied (documentation-only, never executed).
            # 001 (this script) is recorded automatically by if_not_applied
            # after this block exits.
            conn.execute(
                "INSERT OR IGNORE INTO migrations (name) VALUES ('000-initial-schema')"
            )
            print("Created migrations table and seeded 000.", file=sys.stderr)
