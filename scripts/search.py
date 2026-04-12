#!/usr/bin/env -S uv run python
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "boto3>=1.35.0",
#   "sqlite-vec>=0.1.7",
# ]
# ///
"""
CLI semantic search tool for Just My Links.

Downloads the sqlite-vec index from S3, embeds the query via Bedrock Titan V2,
and returns ranked matching URLs.

Usage:
    ./scripts/search.py "my search query"
    ./scripts/search.py "my search query" --env prod
    ./scripts/search.py "my search query" --top 10 --no-cache
"""

import argparse
import json
import os
import struct
import sys
from pathlib import Path

import boto3
import sqlite_vec

try:
    import pysqlite3 as sqlite3
except ImportError:
    import sqlite3  # type: ignore[no-redef]

VECTOR_DB_S3_KEY = "vector-index/index.db"
BEDROCK_MODEL_ID = "amazon.titan-embed-text-v2:0"
EMBEDDING_DIMENSIONS = 1024
DEFAULT_TOP_K = 5

CACHE_DIR = Path.home() / ".cache" / "just-my-links"


def get_bucket_name(env: str) -> str:
    return f"just-my-links--application-bucket--{env}"


def download_index(bucket: str, local_path: Path, s3_client) -> None:
    print(f"Downloading index from s3://{bucket}/{VECTOR_DB_S3_KEY} ...", file=sys.stderr)
    s3_client.download_file(bucket, VECTOR_DB_S3_KEY, str(local_path))


def open_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


def embed_query(text: str, bedrock_client) -> list[float]:
    response = bedrock_client.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps({
            "inputText": text,
            "dimensions": EMBEDDING_DIMENSIONS,
            "normalize": True,
        }),
    )
    return json.loads(response["body"].read())["embedding"]


def serialize_embedding(embedding: list[float]) -> bytes:
    return struct.pack(f"{len(embedding)}f", *embedding)


def search(conn: sqlite3.Connection, embedding: list[float], top_k: int) -> list[tuple[str, float]]:
    """Return [(url, distance), ...] ordered by ascending distance (closer = better)."""
    blob = serialize_embedding(embedding)
    rows = conn.execute(
        """
        SELECT c.url, v.distance
        FROM vec_chunks v
        JOIN chunks c ON c.id = v.chunk_id
        WHERE v.embedding MATCH ?
          AND k = ?
        ORDER BY v.distance
        """,
        (blob, top_k * 3),  # fetch more candidates then deduplicate by URL
    ).fetchall()

    # Deduplicate: keep best (lowest) distance per URL
    seen: dict[str, float] = {}
    for url, dist in rows:
        if url not in seen or dist < seen[url]:
            seen[url] = dist

    results = sorted(seen.items(), key=lambda x: x[1])
    return results[:top_k]


def main() -> None:
    parser = argparse.ArgumentParser(description="Semantic search for Just My Links")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--env", default="dev", help="Environment (default: dev)")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_K, help=f"Number of results (default: {DEFAULT_TOP_K})")
    parser.add_argument("--no-cache", action="store_true", help="Force re-download of index")
    parser.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1)")
    args = parser.parse_args()

    profile = os.environ.get("AWS_PROFILE", "just-my-links")
    session = boto3.Session(profile_name=profile, region_name=args.region)
    s3_client = session.client("s3")
    bedrock_client = session.client("bedrock-runtime")

    bucket = get_bucket_name(args.env)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"index-{args.env}.db"

    if args.no_cache or not cache_path.exists():
        download_index(bucket, cache_path, s3_client)
    else:
        print(f"Using cached index at {cache_path} (use --no-cache to refresh)", file=sys.stderr)

    print(f"Embedding query via Bedrock...", file=sys.stderr)
    embedding = embed_query(args.query, bedrock_client)

    conn = open_db(cache_path)
    results = search(conn, embedding, args.top)
    conn.close()

    if not results:
        print("No results found.")
        return

    print(f"\nTop {len(results)} results for: {args.query!r}\n")
    for i, (url, dist) in enumerate(results, 1):
        # sqlite-vec returns L2 distance for float vectors; lower = more similar
        score = 1 / (1 + dist)  # convert to a 0–1 similarity-ish score
        print(f"  {i}. {url}")
        print(f"     similarity: {score:.4f}  (distance: {dist:.4f})")


if __name__ == "__main__":
    main()
