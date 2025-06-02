# Use the official AWS Lambda Python runtime
FROM public.ecr.aws/lambda/python:3.13

# Copy requirements and install dependencies
COPY pyproject.toml ${LAMBDA_TASK_ROOT}/
RUN pip install uv && \
    uv pip install --system --no-cache-dir -e .

# Copy application code
COPY src/* ${LAMBDA_TASK_ROOT}/

# Set the CMD to your handler
CMD ["app.lambda_handler"]
