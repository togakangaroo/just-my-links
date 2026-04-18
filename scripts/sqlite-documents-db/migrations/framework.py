"""
Migration framework for the just-my-links SQLite database.

Two context managers:

    db_connection(script_path)
        Downloads the DB from S3 (or reads from SQLITE_DB_PATH env var for
        local dev), creates a .bak backup, yields an open sqlite3 connection,
        and uploads the modified DB back to S3 on clean exit.  On error,
        restores from .bak so S3 is never left in a corrupt state.

    if_not_applied(conn, script_path)
        Checks the `migrations` table for the script's stem name.  Yields
        True if the migration should run, False if it was already applied.
        On clean exit when run=True, inserts a row into `migrations`.

Typical migration script usage:

    with db_connection() as conn:
        with if_not_applied(conn, __file__) as run:
            if run:
                conn.execute("ALTER TABLE ...")
"""

import os
import shutil
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

VECTOR_DB_S3_KEY = "vector-index/index.db"
_DEFAULT_LOCAL_PATH = "/tmp/migration-index.db"


def _open_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    try:
        conn.enable_load_extension(True)
        import sqlite_vec  # pyright: ignore[reportMissingImports]

        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except (ImportError, AttributeError):
        pass  # sqlite_vec not installed locally — fine for schema-only migrations
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def db_connection() -> Generator[sqlite3.Connection, None, None]:
    """
    Yield an open sqlite3 connection to the just-my-links DB.

    Resolution order for the DB path:
      1. SQLITE_DB_PATH env var — used as-is (local dev / CI)
      2. S3 bucket (APPLICATION_BUCKET env var) — downloaded to /tmp

    A .bak copy of the DB is made before yielding.  On clean exit the DB is
    uploaded back to S3 (if it came from S3) and the backup is removed.  On
    error the backup is restored before re-raising.
    """
    local_path_override = os.getenv("SQLITE_DB_PATH")
    use_s3 = local_path_override is None

    if use_s3:
        bucket = os.environ.get("APPLICATION_BUCKET")
        if not bucket:
            print(
                "ERROR: Set APPLICATION_BUCKET (and optionally AWS_PROFILE) or "
                "SQLITE_DB_PATH for local dev.",
                file=sys.stderr,
            )
            sys.exit(1)

        import boto3  # pyright: ignore[reportMissingImports]

        s3 = boto3.client("s3")
        db_path = _DEFAULT_LOCAL_PATH

        try:
            print(
                f"Downloading s3://{bucket}/{VECTOR_DB_S3_KEY} → {db_path}",
                file=sys.stderr,
            )
            s3.download_file(bucket, VECTOR_DB_S3_KEY, db_path)
        except s3.exceptions.ClientError as e:  # type: ignore[union-attr]
            if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
                print("No existing DB in S3 — starting fresh.", file=sys.stderr)
                if os.path.exists(db_path):
                    os.remove(db_path)
            else:
                raise
    else:
        db_path = local_path_override
        s3 = None  # type: ignore[assignment]
        bucket = None

    bak_path = db_path + ".bak"
    if os.path.exists(db_path):
        shutil.copy2(db_path, bak_path)

    conn = _open_db(db_path)
    error_occurred = False
    try:
        yield conn
        conn.commit()
    except Exception:
        error_occurred = True
        raise
    finally:
        conn.close()
        if error_occurred:
            if os.path.exists(bak_path):
                shutil.copy2(bak_path, db_path)
                print("Restored DB from backup due to error.", file=sys.stderr)
        else:
            if use_s3:
                print(
                    f"Uploading {db_path} → s3://{bucket}/{VECTOR_DB_S3_KEY}",
                    file=sys.stderr,
                )
                s3.upload_file(db_path, bucket, VECTOR_DB_S3_KEY)  # type: ignore[union-attr]
        if os.path.exists(bak_path):
            os.remove(bak_path)


@contextmanager
def if_not_applied(
    conn: sqlite3.Connection, script_path: str
) -> Generator[bool, None, None]:
    """
    Yield True if this migration should run, False if already applied.

    The migration name is derived from the script filename stem
    (e.g. "002-add-tags-and-titles" for the file of that name).

    On clean exit when run=True, inserts a row into the `migrations` table
    so the migration will be skipped on subsequent runs.  If the migration
    raises an exception the row is NOT inserted.
    """
    name = Path(script_path).stem

    try:
        row = conn.execute(
            "SELECT 1 FROM migrations WHERE name = ?", (name,)
        ).fetchone()
        already_applied = row is not None
    except sqlite3.OperationalError:
        # migrations table doesn't exist yet (expected before 001 runs)
        already_applied = False

    if already_applied:
        print(f"Migration '{name}' already applied — skipping.", file=sys.stderr)
        yield False
        return

    yield True

    conn.execute(
        "INSERT INTO migrations (name, applied_at) VALUES (?, CURRENT_TIMESTAMP)",
        (name,),
    )
    conn.commit()
    print(f"Migration '{name}' applied and recorded.", file=sys.stderr)
