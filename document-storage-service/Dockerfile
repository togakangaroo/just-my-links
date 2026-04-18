# Use the official AWS Lambda Python runtime
FROM public.ecr.aws/lambda/python:3.13

# Copy requirements and install dependencies
COPY document-storage-service/pyproject.toml ${LAMBDA_TASK_ROOT}/
RUN pip install uv && \
    uv pip install --system --no-cache-dir .

# Copy application code
COPY document-storage-service/src/* ${LAMBDA_TASK_ROOT}/

# Set the CMD to your handler
CMD ["app.lambda_handler"]
