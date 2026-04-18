Just My Links is a self-deployed aws-hosted bookmarking/URL-saving service (Pocket replacement). Documents are uploaded via an HTTP API and processed through an event-driven AWS pipeline with ability for semantic search.

Because this is a personal project intended for people to run for their own small-scale use, a key goal is to minimize costs while keeping things secure.

# Development Commands

Each service (`document-storage-service/`, `index-documents-service/`) is an independent Python project. Run commands from within the service directory.

```bash
# Install dependencies (run from a service directory)
uv sync --locked

# Run tests
uv run pytest

# Format + lint (auto-fix)
uv run ruff format .
uv run ruff check --fix .
```

# Deployment

```bash
# Full deploy: builds Docker images, pushes to ECR, deploys CloudFormation
./deploy.sh [environment]                          # defaults to dev
./cloudformation/scripts/validate.sh               # validate templates only

# Deploy a single Lambda service (builds Docker image, pushes to ECR, updates Lambda)
./scripts/deploy-document-storage-service.py [environment]
./scripts/deploy-indexing-service.py [environment]
```

# Architecture

Three Lambda functions: one event-driven ingestion pipeline and one search endpoint.

```
Client → API Gateway → store-document Lambda
                              ↓ S3 (document-storage/<sha256-of-url>/)
                              ↓ EventBridge event: "Document stored"
                              ↓ SQS trigger
                       index-documents Lambda
                              ↓ Embeds document via Amazon Bedrock
                              ↓ Updates SQLite vector index in S3 (vector-index/index.db)
                              ↓ EventBridge event: "Document indexed"

Client → API Gateway → search-documents Lambda
                              ↓ Downloads vector-index/index.db from S3 (5-min local cache)
                              ↓ Embeds query via Amazon Bedrock
                              ↓ Returns ranked results
```

**document-storage-service**: Accepts `PUT /document?url=<url>` with a `multipart/form-data` body. The `document` part must be either `text/html` or `text/plain`. Files are streamed directly to S3 using `StreamingS3Upload` (which handles both small files via `put_object` and larger files via S3 multipart upload). The S3 key prefix is the SHA-256 hash of the URL. A `backup_in_case_of_error` context manager copies existing S3 objects to a `.bak` folder before overwriting, and restores on error.

**index-documents-service**: SQS-triggered. Receives EventBridge events forwarded from SQS. Downloads the document from S3, generates vector embeddings via Amazon Bedrock Titan Embed Text v2, and writes to a SQLite database (with `sqlite-vec` extension) stored in S3 at `vector-index/index.db`.

**search-documents-service**: Accepts `GET /search?q=<query>&top=<n>` (Bearer token required). Downloads and locally caches the SQLite vector index from S3 (5-minute TTL). Supports three search modes returned as sections: `vector` (KNN semantic search), `title` (substring match on normalized titles), `tags` (hashtag filtering — embed tags in query with `#tagname`). `top` defaults to 5, clamped to [1, 20]. Response shape:
```json
{
  "query": "original query",
  "parsed": { "text": "query text", "tags": ["tag1"] },
  "sections": {
    "vector": [{ "url": "...", "distance": 0.12, "title": "..." }],
    "title":  [{ "url": "...", "title": "..." }],
    "tags":   [{ "url": "...", "title": "...", "matched_tags": ["tag1"] }]
  }
}
```
Only non-empty sections are included. Uses Amazon Bedrock Titan Embed Text v2 (1024 dimensions).

**Authentication**: Bearer token validated via constant-time comparison (`secrets.compare_digest`). Token stored in SSM Parameter Store (type `String`, IAM-protected); parameter name passed via `BEARER_TOKEN_PARAM_NAME` env var. Implemented as an `aws-lambda-powertools` middleware.

**Shared AWS resources** (defined in `cloudformation/templates/main.yaml`):
- S3 bucket: `just-my-links-<env>` — stores documents (`document-storage/`) and SQLite vector index (`vector-index/index.db`)
- EventBridge bus: `just-my-links--events--<env>`
- SQS queue: `just-my-links--index-documents-trigger--<env>` (with DLQ)
- All three Lambdas have `reserved_concurrent_executions: 1` (cost control)

# Key Technical Notes

- Both Lambda functions use `aws-lambda-powertools` for structured logging, tracing (X-Ray), and metrics. The `@cache` decorator on env-var getters exploits Lambda container reuse for efficient Secrets Manager access.
- The `MultipartParser` from `python-multipart` uses a callback-based streaming API.
- CloudFormation parameters per environment live in `cloudformation/parameters/<env>.env`.
- When using localstack use awslocal cli and unset the AWS_PROFILE variable beforehand
- For any operations against actual aws the user will take care of logging in, but all operations should be run with the AWS_PROFILE=just-my-links

# Instructions on using beads for memory

This project uses **bd** (beads) for issue tracking. Run `bd onboard` to get started.

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --status in_progress  # Claim work
bd close <id>         # Complete work
bd sync               # Sync with git
```

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
