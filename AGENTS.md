Just My Links is a self-deployed aws-hosted bookmarking/URL-saving service (Pocket replacement). Documents are uploaded via an HTTP API and processed through an event-driven AWS pipeline with ability for semantic search.

Because this is a personal project intended for people to run for their own small-scale use, a key goal is to minimize costs while keeping things secure.

# Development Commands

Each service (`document-storage-service/`, `index-documents-service/`) is an independent Python project. Run commands from within the service directory.

```bash
# Install dependencies (run from a service directory)
uv sync --locked

# Run tests
uv run pytest

# Format code
uv run black .

# Lint
uv run ruff check .
```

# Deployment

```bash
# Deploy CloudFormation infrastructure
./cloudformation/scripts/deploy.sh [environment]   # defaults to dev
./cloudformation/scripts/validate.sh               # validate templates only

# Deploy Lambda services (builds Docker image, pushes to ECR, updates Lambda)
./scripts/deploy-document-storage-service.py [environment]
./scripts/deploy-indexing-service.py [environment]
```

# Architecture

Two Lambda functions connected by an event-driven pipeline:

```
Client → API Gateway → store-document Lambda
                              ↓ S3 (document-storage/<sha256-of-url>/)
                              ↓ EventBridge event: "Document stored"
                              ↓ SQS trigger
                       index-documents Lambda
                              ↓ Index document (downloaded from / uploaded back to S3)
                              ↓ EventBridge event: "Document indexed"
```

**document-storage-service**: Accepts `PUT /document?url=<url>` with a `multipart/form-data` body. The `document` part must be either `text/html` or `text/plain`. Files are streamed directly to S3 using `StreamingS3Upload` (which handles both small files via `put_object` and larger files via S3 multipart upload). The S3 key prefix is the SHA-256 hash of the URL. A `backup_in_case_of_error` context manager copies existing S3 objects to a `.bak` folder before overwriting, and restores on error.

**index-documents-service**: SQS-triggered. Receives EventBridge events forwarded from SQS. ChromaDB integration is a placeholder — the main remaining work is implementing the indexing logic here.

**Authentication**: Bearer token validated via constant-time comparison (`secrets.compare_digest`). Token stored in AWS Secrets Manager; ARN passed via `BEARER_TOKEN_SECRET_ARN` env var. Implemented as an `aws-lambda-powertools` middleware.

**Shared AWS resources** (defined in `cloudformation/templates/main.yaml`):
- S3 bucket: `just-my-links-<env>` — stores documents and ChromaDB file
- EventBridge bus: `just-my-links--events--<env>`
- SQS queue: `just-my-links--index-documents-trigger--<env>` (with DLQ)
- Both Lambdas have `reserved_concurrent_executions: 1` (cost control)

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
