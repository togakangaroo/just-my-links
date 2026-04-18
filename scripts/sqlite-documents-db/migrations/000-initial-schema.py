#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""
Migration 000: Initial schema (documentation only — never executed directly).

This file documents the schema that existed in the DB before the migration
framework was introduced.  It is pre-seeded as "applied" by migration 001
so that the framework knows this state has already been established.

----

Schema at this point in history:

    CREATE TABLE chunks (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        url         TEXT NOT NULL,
        chunk_index INTEGER NOT NULL,
        chunk_text  TEXT
    )

    CREATE VIRTUAL TABLE vec_chunks USING vec0(
        chunk_id  INTEGER PRIMARY KEY,
        embedding float[1024]
    )

    CREATE TABLE documents (
        url   TEXT PRIMARY KEY,
        title TEXT
    )

----

This file intentionally contains no executable code.
"""
