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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            url        TEXT PRIMARY KEY,
            full_title TEXT,
            title      TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS document_tags (
            url  TEXT NOT NULL REFERENCES documents(url),
            tag  TEXT NOT NULL,
            PRIMARY KEY (url, tag)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_document_tags_tag ON document_tags(tag)"
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
    results = app_module._vector_search(conn, query_embedding, top_k=5)
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

    results = app_module._vector_search(conn, base_embedding, top_k=5)
    conn.close()

    urls = [r["url"] for r in results]
    assert urls.count("https://example.com/a") == 1


def test_search_respects_top_k(tmp_path, app_module):
    db_path = str(tmp_path / "test.db")
    conn = _open_test_db(db_path)

    for i in range(5):
        _insert_chunk(conn, f"https://example.com/{i}", 0, _make_embedding(i * 10))

    results = app_module._vector_search(conn, _make_embedding(0), top_k=3)
    conn.close()

    assert len(results) <= 3


def test_search_empty_index(tmp_path, app_module):
    db_path = str(tmp_path / "test.db")
    conn = _open_test_db(db_path)
    results = app_module._vector_search(conn, _make_embedding(1), top_k=5)
    conn.close()
    assert results == []


# ---------------------------------------------------------------------------
# _parse_query
# ---------------------------------------------------------------------------


def test_parse_query_splits_tags_and_text(app_module):
    text, tags = app_module._parse_query("agents #llm planning #ai")
    assert text == "agents planning"
    assert tags == ["llm", "ai"]


def test_parse_query_text_only(app_module):
    text, tags = app_module._parse_query("how do agents reason")
    assert text == "how do agents reason"
    assert tags == []


def test_parse_query_tags_only(app_module):
    text, tags = app_module._parse_query("#llm #agents")
    assert text == ""
    assert tags == ["llm", "agents"]


# ---------------------------------------------------------------------------
# _title_search
# ---------------------------------------------------------------------------


def _insert_document(
    conn: sqlite3.Connection, url: str, full_title: str, title: str
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO documents (url, full_title, title) VALUES (?, ?, ?)",
        (url, full_title, title),
    )
    conn.commit()


def test_title_search_finds_match(tmp_path, app_module):
    db_path = str(tmp_path / "test.db")
    conn = _open_test_db(db_path)
    _insert_document(conn, "https://a.com", "How LLMs Work", "llms work")
    _insert_document(conn, "https://b.com", "Agent Planning", "agent planning")

    results = app_module._title_search(conn, "LLM", top_k=5)
    conn.close()

    urls = [r["url"] for r in results]
    assert "https://a.com" in urls
    assert "https://b.com" not in urls


def test_title_search_empty_after_stop_word_removal(tmp_path, app_module):
    db_path = str(tmp_path / "test.db")
    conn = _open_test_db(db_path)
    _insert_document(conn, "https://a.com", "The Thing", "thing")

    # Query is all stop words — normalises to empty string → no results
    results = app_module._title_search(conn, "the and or", top_k=5)
    conn.close()
    assert results == []


# ---------------------------------------------------------------------------
# _tags_search
# ---------------------------------------------------------------------------


def _insert_tag(conn: sqlite3.Connection, url: str, tag: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO document_tags (url, tag) VALUES (?, ?)", (url, tag)
    )
    conn.commit()


def test_tags_search_finds_match(tmp_path, app_module):
    db_path = str(tmp_path / "test.db")
    conn = _open_test_db(db_path)
    _insert_document(conn, "https://a.com", "LLM article", "llm article")
    _insert_document(conn, "https://b.com", "Other article", "other article")
    _insert_tag(conn, "https://a.com", "llm")

    results = app_module._tags_search(conn, ["llm"], top_k=5)
    conn.close()

    assert len(results) == 1
    assert results[0]["url"] == "https://a.com"
    assert "llm" in results[0]["matched_tags"]


def test_tags_search_ranks_by_match_count(tmp_path, app_module):
    db_path = str(tmp_path / "test.db")
    conn = _open_test_db(db_path)
    _insert_document(conn, "https://a.com", "Two tags", "two tags")
    _insert_document(conn, "https://b.com", "One tag", "one tag")
    _insert_tag(conn, "https://a.com", "llm")
    _insert_tag(conn, "https://a.com", "ai")
    _insert_tag(conn, "https://b.com", "llm")

    results = app_module._tags_search(conn, ["llm", "ai"], top_k=5)
    conn.close()

    assert results[0]["url"] == "https://a.com"  # matched 2 tags


def test_tags_search_empty_tags_returns_empty(tmp_path, app_module):
    db_path = str(tmp_path / "test.db")
    conn = _open_test_db(db_path)
    results = app_module._tags_search(conn, [], top_k=5)
    conn.close()
    assert results == []
