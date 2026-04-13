import io
import json
import os
import struct

try:
    import pysqlite3 as sqlite3  # Lambda's built-in sqlite3 disables enable_load_extension
except ImportError:
    import sqlite3  # type: ignore[no-redef]
from contextlib import contextmanager
from functools import cache
from typing import Any, Generator

import boto3
import sqlite_vec
from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools.utilities.typing import LambdaContext
from bs4 import BeautifulSoup
from pypdf import PdfReader

logger = Logger(level=os.getenv("LOG_LEVEL", "INFO"))
tracer = Tracer()
metrics = Metrics(namespace="just-my-links")

s3_client = boto3.client("s3")
eventbridge_client = boto3.client("events")
bedrock_client = boto3.client("bedrock-runtime")

VECTOR_DB_S3_KEY = "vector-index/index.db"
VECTOR_DB_LOCAL_PATH = "/tmp/index.db"
BEDROCK_MODEL_ID = "amazon.titan-embed-text-v2:0"
EMBEDDING_DIMENSIONS = 1024
# Titan V2 max input is 8192 tokens; we chunk well below that
MAX_CHUNK_CHARS = 2000  # ≈ 500 tokens at ~4 chars/token
OVERLAP_CHARS = 200  # ≈ 50 tokens of overlap between chunks


# ---------------------------------------------------------------------------
# Env vars
# ---------------------------------------------------------------------------


@cache
def get_application_bucket() -> str:
    v = os.getenv("APPLICATION_BUCKET")
    assert v, "APPLICATION_BUCKET environment variable not set"
    return v


@cache
def get_event_bus_name() -> str:
    v = os.getenv("EVENT_BUS_NAME")
    assert v, "EVENT_BUS_NAME environment variable not set"
    return v


# ---------------------------------------------------------------------------
# sqlite-vec helpers
# ---------------------------------------------------------------------------


def _open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(VECTOR_DB_LOCAL_PATH)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
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
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
            chunk_id  INTEGER PRIMARY KEY,
            embedding float[{EMBEDDING_DIMENSIONS}]
        )
    """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            url   TEXT PRIMARY KEY,
            title TEXT
        )
    """
    )
    conn.commit()


def _serialize_embedding(embedding: list[float]) -> bytes:
    return struct.pack(f"{len(embedding)}f", *embedding)


@contextmanager
def sync_vector_db() -> Generator[sqlite3.Connection, None, None]:
    """Download the vector DB from S3, yield an open connection, upload on exit."""
    bucket = get_application_bucket()

    # Download existing index (ok if it doesn't exist yet)
    try:
        s3_client.download_file(bucket, VECTOR_DB_S3_KEY, VECTOR_DB_LOCAL_PATH)
        logger.info("Downloaded existing vector index from S3")
    except s3_client.exceptions.ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey"):
            logger.info("No existing vector index found — starting fresh")
        else:
            raise

    conn = _open_db()
    _init_schema(conn)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
        s3_client.upload_file(VECTOR_DB_LOCAL_PATH, bucket, VECTOR_DB_S3_KEY)
        logger.info("Uploaded updated vector index to S3")


# ---------------------------------------------------------------------------
# HTML → text
# ---------------------------------------------------------------------------


def extract_text(content: bytes | str, content_type: str) -> str:
    """Extract plain text from HTML, plain text, or PDF content."""
    if content_type == "application/pdf":
        raw = content if isinstance(content, bytes) else content.encode("latin-1")
        reader = PdfReader(io.BytesIO(raw))
        pages = [page.extract_text() or "" for page in reader.pages]
        return "\n\n".join(p for p in pages if p.strip())
    text = content.decode("utf-8") if isinstance(content, bytes) else content
    if "html" in content_type:
        soup = BeautifulSoup(text, "html.parser")
        for tag in soup(["script", "style", "head", "nav", "footer"]):
            tag.decompose()
        return soup.get_text(separator="\n\n")
    return text


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def _split_long_text(text: str) -> list[str]:
    """Split text that exceeds MAX_CHUNK_CHARS into overlapping windows."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + MAX_CHUNK_CHARS
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = end - OVERLAP_CHARS  # back up for overlap
    return chunks


MIN_CHUNK_CHARS = 100  # discard nav labels, image captions, lone headings, etc.


def chunk_text(text: str) -> list[str]:
    """Split text on paragraph boundaries; further split paragraphs that are too long."""
    # Normalise whitespace and split on blank lines
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    chunks: list[str] = []
    for para in paragraphs:
        if len(para) < MIN_CHUNK_CHARS:
            continue
        if len(para) <= MAX_CHUNK_CHARS:
            chunks.append(para)
        else:
            chunks.extend(_split_long_text(para))

    return chunks if chunks else [text[:MAX_CHUNK_CHARS]]


# ---------------------------------------------------------------------------
# Bedrock embeddings
# ---------------------------------------------------------------------------


@tracer.capture_method
def embed_text(text: str) -> list[float]:
    """Call Bedrock Titan V2 to get an embedding for a text chunk."""
    response = bedrock_client.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps(
            {
                "inputText": text,
                "dimensions": EMBEDDING_DIMENSIONS,
                "normalize": True,
            }
        ),
    )
    result = json.loads(response["body"].read())
    return result["embedding"]


# ---------------------------------------------------------------------------
# sqlite-vec upsert
# ---------------------------------------------------------------------------


@tracer.capture_method
def upsert_document(conn: sqlite3.Connection, url: str, chunks: list[str], title: str | None = None) -> None:
    """Delete any existing chunks for this URL then insert fresh embeddings."""
    # Find existing chunk ids so we can remove them from the vec table too
    existing_ids = [
        row[0] for row in conn.execute("SELECT id FROM chunks WHERE url = ?", (url,))
    ]
    if existing_ids:
        placeholders = ",".join("?" * len(existing_ids))
        conn.execute(
            f"DELETE FROM vec_chunks WHERE chunk_id IN ({placeholders})", existing_ids
        )
        conn.execute("DELETE FROM chunks WHERE url = ?", (url,))

    for i, chunk in enumerate(chunks):
        embedding = embed_text(chunk)
        cur = conn.execute(
            "INSERT INTO chunks (url, chunk_index, chunk_text) VALUES (?, ?, ?)",
            (url, i, chunk),
        )
        chunk_id = cur.lastrowid
        conn.execute(
            "INSERT INTO vec_chunks (chunk_id, embedding) VALUES (?, ?)",
            (chunk_id, _serialize_embedding(embedding)),
        )

    conn.execute(
        "INSERT INTO documents (url, title) VALUES (?, ?) ON CONFLICT(url) DO UPDATE SET title = excluded.title",
        (url, title),
    )

    logger.info(
        "Upserted document chunks", extra={"url": url, "chunk_count": len(chunks)}
    )


# ---------------------------------------------------------------------------
# Event processing
# ---------------------------------------------------------------------------


@tracer.capture_method
def process_sqs_record(record: dict[str, Any]) -> None:
    message_body = json.loads(record["body"])
    event_detail = message_body.get("detail", {})
    folder_path = event_detail.get("folderPath")
    document_url = event_detail.get("documentUrl")

    logger.info(
        "Processing document indexing event",
        extra={
            "folder_path": folder_path,
            "document_url": document_url,
            "message_id": record.get("messageId"),
        },
    )

    bucket = get_application_bucket()

    # Read .metadata.json to find the entrypoint file
    metadata_key = f"{folder_path}/.metadata.json"
    metadata = json.loads(
        s3_client.get_object(Bucket=bucket, Key=metadata_key)["Body"].read()
    )
    entrypoint = metadata["entrypoint"]
    document_title = metadata.get("title")
    if entrypoint.endswith(".html"):
        content_type = "text/html"
    elif entrypoint.endswith(".pdf"):
        content_type = "application/pdf"
    else:
        content_type = "text/plain"

    # Read the document content
    doc_key = f"{folder_path}/{entrypoint}"
    raw_content = s3_client.get_object(Bucket=bucket, Key=doc_key)["Body"].read()

    # Extract, chunk, embed, store
    text = extract_text(raw_content, content_type)
    chunks = chunk_text(text)
    logger.info(
        "Chunked document", extra={"url": document_url, "chunk_count": len(chunks)}
    )

    with sync_vector_db() as conn:
        upsert_document(conn, document_url, chunks, title=document_title)

    # Publish "Document indexed" event
    eventbridge_client.put_events(
        Entries=[
            {
                "Source": "just-my-links.index-documents",
                "DetailType": "Document indexed",
                "Detail": json.dumps(
                    {"folderPath": folder_path, "documentUrl": document_url}
                ),
                "EventBusName": get_event_bus_name(),
            }
        ]
    )

    metrics.add_metric(name="DocumentsIndexed", unit=MetricUnit.Count, value=1)


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------


@logger.inject_lambda_context()
@tracer.capture_lambda_handler
@metrics.log_metrics
def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    records = event.get("Records", [])
    logger.info(
        "Lambda handler invoked",
        extra={
            "record_count": len(records),
            "function_name": context.function_name,
        },
    )

    for record in records:
        process_sqs_record(record)

    logger.info("Successfully processed all SQS records")
    return {
        "statusCode": 200,
        "body": json.dumps({"processed_count": len(records)}),
    }
