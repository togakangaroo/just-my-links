# Integration Test Fixtures

## `fixture.db`

A SQLite database pre-loaded with sample data, used by integration tests for
the index and search services.  It contains the schema produced after all
migrations up to and including **002-add-tags-and-titles** have been applied.

### Schema

```sql
-- Text chunks produced by the indexing pipeline
CREATE TABLE chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    chunk_text  TEXT
);

-- Per-document metadata
CREATE TABLE documents (
    url        TEXT PRIMARY KEY,
    full_title TEXT,   -- raw user-supplied title, # marks stripped (display)
    title      TEXT    -- lower-cased, stop words + # marks stripped (search)
);

-- Tags extracted from hashtag-prefixed words in the user-supplied title
CREATE TABLE document_tags (
    url  TEXT NOT NULL REFERENCES documents(url),
    tag  TEXT NOT NULL,
    PRIMARY KEY (url, tag)
);
CREATE INDEX idx_document_tags_tag ON document_tags(tag);

-- Applied migration records
CREATE TABLE migrations (
    name       TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- sqlite-vec virtual table (extension must be loaded to access)
CREATE VIRTUAL TABLE vec_chunks USING vec0(
    chunk_id  INTEGER PRIMARY KEY,
    embedding float[1024]
);
```

### Sample data

| url | full_title | title | tags |
|-----|-----------|-------|------|
| https://example.com/a | How LLMs work | llms work | _(none)_ |
| https://example.com/b | Agent planning deep dive | agent planning llm ai deep dive | llm, ai |
| https://example.com/c | _(null)_ | _(null)_ | _(none)_ |

### Using in tests

Set `SQLITE_DB_PATH` to the fixture path so tests bypass S3:

```python
import os, shutil, tempfile

@pytest.fixture
def db_path(tmp_path):
    fixture = Path(__file__).parent / "fixture.db"
    copy = tmp_path / "index.db"
    shutil.copy(fixture, copy)
    return str(copy)
```

Then in the test, point the service at the copy via `SQLITE_DB_PATH` or by
patching `VECTOR_DB_LOCAL_PATH`.

### Keeping it up to date

Re-generate `fixture.db` whenever the schema changes:

```bash
# Run all migrations against a fresh local copy, then replace the fixture
rm -f /tmp/fixture-regen.db
SQLITE_DB_PATH=/tmp/fixture-regen.db uv run scripts/sqlite-documents-db/migrations/001-add-migrations-table.py
# (seed sample data manually or via a helper script)
SQLITE_DB_PATH=/tmp/fixture-regen.db uv run scripts/sqlite-documents-db/migrations/002-add-tags-and-titles.py
cp /tmp/fixture-regen.db tests/integration/fixture.db
```
