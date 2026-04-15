"""Tests for search-documents-service."""

import math
import sqlite3
import struct
import sys
from unittest.mock import MagicMock, patch

import pytest
import sqlite_vec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_embedding(seed: int, dims: int = 1024) -> list[float]:
    return [math.sin(seed + i) for i in range(dims)]


def _serialize(embedding: list[float]) -> bytes:
    return struct.pack(f"{len(embedding)}f", *embedding)


def _open_test_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            url         TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            chunk_text  TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
            chunk_id  INTEGER PRIMARY KEY,
            embedding float[1024]
        )
        """
    )
    conn.commit()
    return conn


def _insert_chunk(
    conn: sqlite3.Connection, url: str, chunk_index: int, embedding: list[float]
) -> None:
    cur = conn.execute(
        "INSERT INTO chunks (url, chunk_index, chunk_text) VALUES (?, ?, ?)",
        (url, chunk_index, "test chunk"),
    )
    conn.execute(
        "INSERT INTO vec_chunks (chunk_id, embedding) VALUES (?, ?)",
        (cur.lastrowid, _serialize(embedding)),
    )
    conn.commit()


@pytest.fixture
def app_module(monkeypatch, tmp_path):
    monkeypatch.setenv("APPLICATION_BUCKET", "test-bucket")
    monkeypatch.setenv("BEARER_TOKEN_PARAM_NAME", "/just-my-links/auth-token/test")
    sys.modules.pop("app", None)

    mock_s3 = MagicMock()
    mock_secrets = MagicMock()
    mock_bedrock = MagicMock()

    def client_factory(service, **kwargs):
        if service == "s3":
            return mock_s3
        elif service == "ssm":
            return mock_secrets
        elif service == "bedrock-runtime":
            return mock_bedrock
        return MagicMock()

    with patch("boto3.client", side_effect=client_factory):
        import app as m

    m.get_application_bucket.cache_clear()
    m.get_bearer_token.cache_clear()
    monkeypatch.setattr(m, "VECTOR_DB_LOCAL_PATH", str(tmp_path / "index.db"))
    return m


# ---------------------------------------------------------------------------
# search_index tests (use a real in-memory sqlite-vec DB)
# ---------------------------------------------------------------------------


def test_search_returns_closest_match(tmp_path, app_module):
    db_path = str(tmp_path / "test.db")
    conn = _open_test_db(db_path)

    target = _make_embedding(1)
    far = _make_embedding(500)

    _insert_chunk(conn, "https://example.com/target", 0, target)
    _insert_chunk(conn, "https://example.com/far", 0, far)

    query_embedding = _make_embedding(1)  # identical to target
    results = app_module.search_index(conn, query_embedding, top_k=5)
    conn.close()

    assert len(results) >= 1
    assert results[0]["url"] == "https://example.com/target"
    assert results[0]["distance"] < results[-1]["distance"] or len(results) == 1


def test_search_deduplicates_by_url(tmp_path, app_module):
    """Multiple chunks from the same URL should be collapsed to the best match."""
    db_path = str(tmp_path / "test.db")
    conn = _open_test_db(db_path)

    base_embedding = _make_embedding(1)
    slightly_different = _make_embedding(2)

    _insert_chunk(conn, "https://example.com/a", 0, base_embedding)
    _insert_chunk(conn, "https://example.com/a", 1, slightly_different)
    _insert_chunk(conn, "https://example.com/b", 0, _make_embedding(100))

    results = app_module.search_index(conn, base_embedding, top_k=5)
    conn.close()

    urls = [r["url"] for r in results]
    assert urls.count("https://example.com/a") == 1


def test_search_respects_top_k(tmp_path, app_module):
    db_path = str(tmp_path / "test.db")
    conn = _open_test_db(db_path)

    for i in range(5):
        _insert_chunk(conn, f"https://example.com/{i}", 0, _make_embedding(i * 10))

    results = app_module.search_index(conn, _make_embedding(0), top_k=3)
    conn.close()

    assert len(results) <= 3


def test_search_empty_index(tmp_path, app_module):
    db_path = str(tmp_path / "test.db")
    conn = _open_test_db(db_path)
    results = app_module.search_index(conn, _make_embedding(1), top_k=5)
    conn.close()
    assert results == []
