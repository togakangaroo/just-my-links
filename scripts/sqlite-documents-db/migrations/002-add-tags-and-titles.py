#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "boto3>=1.35",
# ]
# ///
"""
Migration 002: Add document_tags table and split title into full_title / title.

Schema changes:
  - documents.full_title TEXT  — raw user-supplied title (# marks stripped for display)
  - documents.title TEXT       — lower-cased, # marks + stop words stripped (search)
  - document_tags (url, tag)   — composite PK, index on tag

Data migration for existing rows in `documents`:
  - full_title  ← existing title value (strip leading/trailing # marks)
  - title       ← normalised (lower, # stripped, stop words removed)
  - tags        ← words that were prefixed with # in the original title

Usage (local dev — point at a copy of the DB):
    SQLITE_DB_PATH=/tmp/my-local-copy.db uv run scripts/sqlite-documents-db/migrations/002-add-tags-and-titles.py

Usage (against real S3 DB):
    AWS_PROFILE=just-my-links APPLICATION_BUCKET=just-my-links-dev \\
        uv run scripts/sqlite-documents-db/migrations/002-add-tags-and-titles.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from framework import db_connection, if_not_applied  # noqa: E402

# ---------------------------------------------------------------------------
# Stop words — must stay in sync with index-documents-service and
# search-documents-service (copied here so the script is self-contained).
# ---------------------------------------------------------------------------
STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "not",
        "no",
        "nor",
        "so",
        "yet",
        "both",
        "either",
        "neither",
        "that",
        "this",
        "these",
        "those",
        "it",
        "its",
        "itself",
        "he",
        "she",
        "they",
        "them",
        "their",
        "what",
        "which",
        "who",
        "whom",
        "when",
        "where",
        "why",
        "how",
        "all",
        "each",
        "every",
        "any",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "into",
        "through",
        "as",
        "if",
        "up",
        "about",
        "against",
        "between",
        "because",
        "than",
        "after",
        "before",
        "during",
        "under",
        "over",
        "then",
    }
)


def _parse_title(raw: str) -> tuple[str, str, list[str]]:
    """
    Parse a raw title string into (full_title, normalized_title, tags).

    full_title   — raw input with # marks stripped (for display)
    title        — lower-cased, stop words removed (for search)
    tags         — words that carried a # prefix, lower-cased, # stripped
    """
    tags: list[str] = []
    non_tag_words: list[str] = []

    for word in raw.split():
        if word.startswith("#"):
            tag = word.lstrip("#").lower()
            if tag:
                tags.append(tag)
        else:
            non_tag_words.append(word)

    full_title = " ".join(non_tag_words)  # # words removed for display

    # Normalize: lower + stop-word filter across all meaningful words
    all_words = raw.replace("#", "").lower().split()
    normalized = " ".join(w for w in all_words if w and w not in STOP_WORDS)

    return full_title, normalized, tags


with db_connection() as conn:
    with if_not_applied(conn, __file__) as run:
        if run:
            # ------------------------------------------------------------------
            # Schema changes
            # ------------------------------------------------------------------
            conn.execute("ALTER TABLE documents ADD COLUMN full_title TEXT")
            conn.execute(
                """
                CREATE TABLE document_tags (
                    url  TEXT NOT NULL REFERENCES documents(url),
                    tag  TEXT NOT NULL,
                    PRIMARY KEY (url, tag)
                )
                """
            )
            conn.execute("CREATE INDEX idx_document_tags_tag ON document_tags(tag)")

            # ------------------------------------------------------------------
            # Data migration: populate full_title from existing title values
            # and extract tags
            # ------------------------------------------------------------------
            rows = conn.execute(
                "SELECT url, title FROM documents WHERE title IS NOT NULL"
            ).fetchall()

            migrated = 0
            for url, raw_title in rows:
                full_title, normalized, tags = _parse_title(raw_title)

                conn.execute(
                    "UPDATE documents SET full_title = ?, title = ? WHERE url = ?",
                    (full_title, normalized, url),
                )
                for tag in tags:
                    conn.execute(
                        "INSERT OR IGNORE INTO document_tags (url, tag) VALUES (?, ?)",
                        (url, tag),
                    )
                migrated += 1

            print(
                f"Schema updated; migrated {migrated} document(s).",
                file=sys.stderr,
            )
