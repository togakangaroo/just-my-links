# Use the official AWS Lambda Python runtime
FROM public.ecr.aws/lambda/python:3.13

# Install build deps needed to compile pysqlite3 from source
RUN dnf install -y gcc sqlite-devel

# Copy requirements and install dependencies
COPY search-documents-service/pyproject.toml ${LAMBDA_TASK_ROOT}/
RUN pip install uv && \
    uv pip install --system --no-cache-dir -e .

# Copy application code (symlinks are resolved against the project-root build context)
COPY search-documents-service/src/* ${LAMBDA_TASK_ROOT}/

# Set the CMD to your handler
CMD ["app.lambda_handler"]
