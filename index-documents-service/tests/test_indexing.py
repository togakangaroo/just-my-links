"""Tests for index-documents-service processing logic."""

import json
import sqlite3
import struct
import sys
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_sqs_record(folder_path: str, document_url: str) -> dict:
    return {
        "messageId": "test-msg-001",
        "body": json.dumps(
            {
                "detail": {
                    "folderPath": folder_path,
                    "documentUrl": document_url,
                }
            }
        ),
    }


def deserialize_embedding(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


@pytest.fixture
def app_module(monkeypatch, tmp_path):
    monkeypatch.setenv("APPLICATION_BUCKET", "test-bucket")
    monkeypatch.setenv("EVENT_BUS_NAME", "test-bus")
    sys.modules.pop("app", None)

    mock_s3 = MagicMock()
    mock_eventbridge = MagicMock()
    mock_bedrock = MagicMock()

    def client_factory(service, **kwargs):
        if service == "s3":
            return mock_s3
        elif service == "events":
            return mock_eventbridge
        elif service == "bedrock-runtime":
            return mock_bedrock
        return MagicMock()

    with patch("boto3.client", side_effect=client_factory):
        import app as m

    m.get_application_bucket.cache_clear()
    m.get_event_bus_name.cache_clear()
    monkeypatch.setattr(m, "VECTOR_DB_LOCAL_PATH", str(tmp_path / "index.db"))
    return m


# ---------------------------------------------------------------------------
# extract_text
# ---------------------------------------------------------------------------


def test_extract_text_strips_html(app_module):
    html = "<html><head><title>T</title></head><body><p>Hello</p><script>bad()</script></body></html>"
    result = app_module.extract_text(html, "text/html")
    assert "Hello" in result
    assert "bad()" not in result
    assert "<p>" not in result


def test_extract_text_passthrough_for_plain(app_module):
    text = "Just plain text."
    assert app_module.extract_text(text, "text/plain") == text


def test_extract_text_extracts_pdf(app_module):
    """extract_text should pull text out of a PDF byte stream."""
    import io
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    pdf_bytes = buf.getvalue()

    # A blank page has no text; just verify it doesn't crash and returns a string.
    result = app_module.extract_text(pdf_bytes, "application/pdf")
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# chunk_text
# ---------------------------------------------------------------------------


def test_chunk_text_splits_on_paragraph_boundaries(app_module):
    para1 = "This is the first paragraph of the article. " * 3  # ~132 chars, above MIN
    para2 = "This is the second paragraph of the article. " * 3  # ~138 chars, above MIN
    text = f"{para1}\n\n{para2}"
    chunks = app_module.chunk_text(text)
    assert len(chunks) == 2
    assert chunks[0] == para1.strip()
    assert chunks[1] == para2.strip()


def test_chunk_text_filters_short_paragraphs(app_module):
    short = "Too short."
    long_para = (
        "This paragraph is long enough to survive the minimum length filter. " * 2
    )
    text = f"{short}\n\n{long_para}"
    chunks = app_module.chunk_text(text)
    assert len(chunks) == 1
    assert chunks[0] == long_para.strip()


def test_chunk_text_long_paragraph_is_split(app_module):
    # A paragraph longer than MAX_CHUNK_CHARS should be split
    long_para = "word " * 600  # well over MAX_CHUNK_CHARS (2000 chars)
    chunks = app_module.chunk_text(long_para)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= app_module.MAX_CHUNK_CHARS


def test_chunk_text_overlap(app_module):
    # Verify that consecutive chunks share some text at the boundary
    long_para = "x" * (app_module.MAX_CHUNK_CHARS * 2 + 100)
    chunks = app_module.chunk_text(long_para)
    assert len(chunks) >= 2
    # The start of chunk[1] should be the last OVERLAP_CHARS of chunk[0]
    tail_of_first = chunks[0][-app_module.OVERLAP_CHARS :]
    head_of_second = chunks[1][: app_module.OVERLAP_CHARS]
    assert tail_of_first == head_of_second


def test_chunk_text_empty_string_returns_one_chunk(app_module):
    # Even a degenerate input shouldn't crash
    chunks = app_module.chunk_text("  \n\n  ")
    assert len(chunks) == 1


# ---------------------------------------------------------------------------
# upsert_document (using a real in-memory sqlite-vec DB)
# ---------------------------------------------------------------------------


def _make_fake_embedding(seed: int, dims: int = 1024) -> list[float]:
    """Return a deterministic unit-ish vector for testing."""
    import math

    return [math.sin(seed + i) for i in range(dims)]


def _open_test_db(path: str, app_module) -> sqlite3.Connection:
    """Open a test DB with the same init logic as the app."""
    conn = (
        app_module._open_db.__wrapped__(path)
        if hasattr(app_module._open_db, "__wrapped__")
        else None
    )
    # Just use the app's helpers directly
    conn = sqlite3.connect(path)
    conn.enable_load_extension(True)
    import sqlite_vec

    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    app_module._init_schema(conn)
    return conn


def test_upsert_inserts_chunks(app_module, tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("app.VECTOR_DB_LOCAL_PATH", db_path)

    call_count = 0

    def fake_embed(text):
        nonlocal call_count
        call_count += 1
        return _make_fake_embedding(call_count)

    monkeypatch.setattr("app.embed_text", fake_embed)

    conn = _open_test_db(db_path, app_module)
    chunks = ["chunk one", "chunk two", "chunk three"]
    app_module.upsert_document(conn, "https://example.com/a", chunks)
    conn.commit()

    rows = list(
        conn.execute("SELECT url, chunk_index FROM chunks ORDER BY chunk_index")
    )
    assert len(rows) == 3
    assert all(r[0] == "https://example.com/a" for r in rows)

    vec_rows = list(conn.execute("SELECT chunk_id FROM vec_chunks"))
    assert len(vec_rows) == 3
    conn.close()


def test_upsert_replaces_existing_chunks(app_module, tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("app.VECTOR_DB_LOCAL_PATH", db_path)

    counter = [0]

    def fake_embed(text):
        counter[0] += 1
        return _make_fake_embedding(counter[0])

    monkeypatch.setattr("app.embed_text", fake_embed)

    conn = _open_test_db(db_path, app_module)

    # First index: 3 chunks
    app_module.upsert_document(conn, "https://example.com/a", ["a", "b", "c"])
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 3

    # Re-index with 2 chunks — old 3 should be gone
    app_module.upsert_document(conn, "https://example.com/a", ["x", "y"])
    conn.commit()
    rows = list(conn.execute("SELECT chunk_index FROM chunks ORDER BY chunk_index"))
    assert len(rows) == 2
    assert conn.execute("SELECT COUNT(*) FROM vec_chunks").fetchone()[0] == 2
    conn.close()


def test_upsert_different_urls_coexist(app_module, tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("app.VECTOR_DB_LOCAL_PATH", db_path)

    counter = [0]

    def fake_embed(text):
        counter[0] += 1
        return _make_fake_embedding(counter[0])

    monkeypatch.setattr("app.embed_text", fake_embed)

    conn = _open_test_db(db_path, app_module)
    app_module.upsert_document(conn, "https://example.com/a", ["chunk a"])
    app_module.upsert_document(conn, "https://example.com/b", ["chunk b1", "chunk b2"])
    conn.commit()

    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 3
    conn.close()


def test_upsert_stores_title(app_module, tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("app.VECTOR_DB_LOCAL_PATH", db_path)

    counter = [0]

    def fake_embed(text):
        counter[0] += 1
        return _make_fake_embedding(counter[0])

    monkeypatch.setattr("app.embed_text", fake_embed)

    conn = _open_test_db(db_path, app_module)
    app_module.upsert_document(
        conn, "https://example.com/a", ["chunk"], title="My Great Article"
    )
    conn.commit()

    row = conn.execute(
        "SELECT full_title, title FROM documents WHERE url = ?",
        ("https://example.com/a",),
    ).fetchone()
    assert row is not None
    assert row[0] == "My Great Article"  # full_title: original, no # tokens
    assert row[1] == "my great article"  # title: normalised (lower, stop words out)
    conn.close()


def test_upsert_title_none_stored_as_null(app_module, tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("app.VECTOR_DB_LOCAL_PATH", db_path)

    counter = [0]

    def fake_embed(text):
        counter[0] += 1
        return _make_fake_embedding(counter[0])

    monkeypatch.setattr("app.embed_text", fake_embed)

    conn = _open_test_db(db_path, app_module)
    app_module.upsert_document(conn, "https://example.com/a", ["chunk"])
    conn.commit()

    row = conn.execute(
        "SELECT full_title, title FROM documents WHERE url = ?",
        ("https://example.com/a",),
    ).fetchone()
    assert row is not None
    assert row[0] is None
    assert row[1] is None
    conn.close()


def test_upsert_updates_title_on_reindex(app_module, tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("app.VECTOR_DB_LOCAL_PATH", db_path)

    counter = [0]

    def fake_embed(text):
        counter[0] += 1
        return _make_fake_embedding(counter[0])

    monkeypatch.setattr("app.embed_text", fake_embed)

    conn = _open_test_db(db_path, app_module)
    app_module.upsert_document(
        conn, "https://example.com/a", ["chunk"], title="Old Title"
    )
    conn.commit()
    app_module.upsert_document(
        conn, "https://example.com/a", ["chunk"], title="New Title"
    )
    conn.commit()

    row = conn.execute(
        "SELECT full_title FROM documents WHERE url = ?", ("https://example.com/a",)
    ).fetchone()
    assert row[0] == "New Title"
    conn.close()


def test_parse_title_extracts_tags(app_module):
    full_title, normalized, tags = app_module._parse_title(
        "Agent planning #llm #ai deep dive"
    )
    assert full_title == "Agent planning deep dive"
    assert tags == ["llm", "ai"]
    assert "llm" in normalized
    assert "ai" in normalized


def test_parse_title_no_tags(app_module):
    full_title, normalized, tags = app_module._parse_title("How LLMs work")
    assert full_title == "How LLMs work"
    assert tags == []
    assert "llms" in normalized


def test_upsert_stores_tags(app_module, tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("app.VECTOR_DB_LOCAL_PATH", db_path)

    counter = [0]

    def fake_embed(text):
        counter[0] += 1
        return _make_fake_embedding(counter[0])

    monkeypatch.setattr("app.embed_text", fake_embed)

    conn = _open_test_db(db_path, app_module)
    app_module.upsert_document(
        conn,
        "https://example.com/a",
        ["chunk"],
        title="Planning for #llm #ai systems",
    )
    conn.commit()

    tags = [
        row[0]
        for row in conn.execute(
            "SELECT tag FROM document_tags WHERE url = ? ORDER BY tag",
            ("https://example.com/a",),
        )
    ]
    assert tags == ["ai", "llm"]
    conn.close()


def test_upsert_replaces_tags_on_reindex(app_module, tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr("app.VECTOR_DB_LOCAL_PATH", db_path)

    counter = [0]

    def fake_embed(text):
        counter[0] += 1
        return _make_fake_embedding(counter[0])

    monkeypatch.setattr("app.embed_text", fake_embed)

    conn = _open_test_db(db_path, app_module)
    app_module.upsert_document(
        conn, "https://example.com/a", ["chunk"], title="First #old tag"
    )
    conn.commit()
    app_module.upsert_document(
        conn, "https://example.com/a", ["chunk"], title="Second #new tag"
    )
    conn.commit()

    tags = [
        row[0]
        for row in conn.execute(
            "SELECT tag FROM document_tags WHERE url = ? ORDER BY tag",
            ("https://example.com/a",),
        )
    ]
    assert tags == ["new"]  # "old" tag should be gone
    conn.close()
