import json
import os
import secrets
import struct
import time

try:
    import pysqlite3 as sqlite3  # Lambda's built-in sqlite3 disables enable_load_extension
except ImportError:
    import sqlite3  # type: ignore[no-redef]
from functools import cache
from typing import Any, Dict

import boto3
import sqlite_vec
from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver, Response, content_types
from aws_lambda_powertools.event_handler.middlewares import NextMiddleware
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger(level=os.getenv("LOG_LEVEL", "INFO"))
tracer = Tracer()
metrics = Metrics(namespace="just-my-links")
app = APIGatewayHttpResolver()

secrets_client = boto3.client("secretsmanager")
s3_client = boto3.client("s3")
bedrock_client = boto3.client("bedrock-runtime")

VECTOR_DB_S3_KEY = "vector-index/index.db"
VECTOR_DB_LOCAL_PATH = "/tmp/index.db"
BEDROCK_MODEL_ID = "amazon.titan-embed-text-v2:0"
EMBEDDING_DIMENSIONS = 1024
INDEX_CACHE_TTL_SECONDS = 300  # refresh index from S3 every 5 minutes

_index_last_downloaded: float = 0.0


# ---------------------------------------------------------------------------
# Env / secrets helpers
# ---------------------------------------------------------------------------


@cache
def get_application_bucket() -> str:
    v = os.getenv("APPLICATION_BUCKET")
    assert v, "APPLICATION_BUCKET environment variable not set"
    return v


@cache
def get_bearer_token() -> str:
    secret_arn = os.getenv("BEARER_TOKEN_SECRET_ARN")
    assert secret_arn, "BEARER_TOKEN_SECRET_ARN environment variable not set"
    response = secrets_client.get_secret_value(SecretId=secret_arn)
    return response["SecretString"]


# ---------------------------------------------------------------------------
# Auth middleware (same pattern as document-storage-service)
# ---------------------------------------------------------------------------


def _unauthorized() -> Response:
    metrics.add_metric(name="UnauthorizedRequests", unit=MetricUnit.Count, value=1)
    return Response(
        status_code=401,
        content_type=content_types.APPLICATION_JSON,
        body={"error": "Unauthorized"},
    )


def authentication_middleware(app: APIGatewayHttpResolver, next_middleware: NextMiddleware) -> Response:
    headers = getattr(app.current_event, "headers", None) or {}
    auth_header = headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return _unauthorized()
    provided_token = auth_header[7:]
    if not secrets.compare_digest(provided_token, get_bearer_token()):
        return _unauthorized()
    return next_middleware(app)


app.use(middlewares=[authentication_middleware])


# ---------------------------------------------------------------------------
# Vector DB helpers
# ---------------------------------------------------------------------------


def _ensure_index_fresh() -> None:
    """Download index.db from S3 if missing or stale (TTL-based)."""
    global _index_last_downloaded
    now = time.monotonic()
    if os.path.exists(VECTOR_DB_LOCAL_PATH) and (now - _index_last_downloaded) < INDEX_CACHE_TTL_SECONDS:
        logger.debug("Using cached index", extra={"age_seconds": now - _index_last_downloaded})
        return
    bucket = get_application_bucket()
    logger.info("Downloading vector index from S3")
    s3_client.download_file(bucket, VECTOR_DB_S3_KEY, VECTOR_DB_LOCAL_PATH)
    _index_last_downloaded = time.monotonic()


def _open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(VECTOR_DB_LOCAL_PATH)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    # Ensure documents table exists for older indexes that predate title support
    conn.execute("CREATE TABLE IF NOT EXISTS documents (url TEXT PRIMARY KEY, title TEXT)")
    return conn


def _serialize_embedding(embedding: list[float]) -> bytes:
    return struct.pack(f"{len(embedding)}f", *embedding)


# ---------------------------------------------------------------------------
# Core search logic
# ---------------------------------------------------------------------------


@tracer.capture_method
def embed_query(text: str) -> list[float]:
    response = bedrock_client.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps({
            "inputText": text,
            "dimensions": EMBEDDING_DIMENSIONS,
            "normalize": True,
        }),
    )
    return json.loads(response["body"].read())["embedding"]


@tracer.capture_method
def search_index(conn: sqlite3.Connection, embedding: list[float], top_k: int) -> list[dict]:
    """KNN search; deduplicate by URL keeping best (lowest) distance per document."""
    blob = _serialize_embedding(embedding)
    rows = conn.execute(
        """
        SELECT c.url, v.distance, d.title
        FROM vec_chunks v
        JOIN chunks c ON c.id = v.chunk_id
        LEFT JOIN documents d ON d.url = c.url
        WHERE v.embedding MATCH ?
          AND k = ?
        ORDER BY v.distance
        """,
        (blob, top_k * 3),  # over-fetch then deduplicate
    ).fetchall()

    seen: dict[str, tuple[float, str | None]] = {}
    for url, dist, title in rows:
        if url not in seen or dist < seen[url][0]:
            seen[url] = (dist, title)

    results = sorted(seen.items(), key=lambda x: x[1][0])[:top_k]
    return [{"url": url, "distance": dist, "title": title} for url, (dist, title) in results]


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@app.get("/search")
@tracer.capture_method
def search() -> Response:
    params = app.current_event.query_string_parameters or {}
    query = params.get("q", "").strip()
    if not query:
        return Response(
            status_code=400,
            content_type=content_types.APPLICATION_JSON,
            body={"error": "Missing required query parameter 'q'"},
        )

    try:
        top_k = int(params.get("top", "5"))
        top_k = max(1, min(top_k, 20))
    except ValueError:
        top_k = 5

    logger.info("Search request", extra={"query": query, "top_k": top_k})

    _ensure_index_fresh()
    embedding = embed_query(query)

    conn = _open_db()
    try:
        results = search_index(conn, embedding, top_k)
    finally:
        conn.close()

    metrics.add_metric(name="SearchRequests", unit=MetricUnit.Count, value=1)
    logger.info("Search complete", extra={"result_count": len(results)})

    return Response(
        status_code=200,
        content_type=content_types.APPLICATION_JSON,
        body={"query": query, "results": results},
    )


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)
@tracer.capture_lambda_handler
@metrics.log_metrics
def lambda_handler(event: Dict[str, Any], context: LambdaContext) -> Dict[str, Any]:
    return app.resolve(event, context)
