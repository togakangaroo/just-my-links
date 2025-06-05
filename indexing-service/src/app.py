import json
import os
import secrets
from functools import cache
from typing import Dict, Any

import boto3
from aws_lambda_powertools import Logger, Tracer, Metrics
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools.event_handler import APIGatewayRestResolver, Response, content_types
from aws_lambda_powertools.event_handler.middlewares import NextMiddleware
from aws_lambda_powertools.utilities.typing import LambdaContext

# Initialize powertools
logger = Logger(level=os.getenv("LOG_LEVEL", "INFO"))
tracer = Tracer()
metrics = Metrics(namespace="just-my-links")
app = APIGatewayRestResolver()

# Initialize AWS clients
secrets_client = boto3.client('secretsmanager')


@cache
def get_bearer_token() -> str:
    """Get bearer token from Secrets Manager with caching"""
    secret_arn = os.getenv("BEARER_TOKEN_SECRET_ARN")
    
    if not secret_arn:
        logger.error("BEARER_TOKEN_SECRET_ARN environment variable not set")
        raise ValueError("BEARER_TOKEN_SECRET_ARN environment variable not set")

    try:
        logger.debug("Getting bearer token from Secrets Manager", extra={"secret_arn":secret_arn})
        response = secrets_client.get_secret_value(SecretId=secret_arn)
        logger.debug("Bearer token retrieved from Secrets Manager")
        return response['SecretString']
    except Exception as e:
        logger.error("Failed to retrieve bearer token", extra={"error": str(e), "secret_arn": secret_arn})
        raise

def _unauthorized_request() -> Response:
    metrics.add_metric(name="UnauthorizedRequests", unit=MetricUnit.Count, value=1)
    return Response(
        status_code=401,
        content_type=content_types.APPLICATION_JSON,
        body={"error": "Unauthorized"})


def authentication_middleware(app: APIGatewayRestResolver, next_middleware: NextMiddleware) -> Response:
    """Middleware to authenticate requests using bearer token"""
    auth_header = app.current_event.headers.get("Authorization", "")

    if not auth_header.startswith("Bearer "):
        return _unauthorized_request()

    provided_token = auth_header[7:]  # Remove "Bearer " prefix
    expected_token = get_bearer_token()

    is_valid = secrets.compare_digest(provided_token, expected_token)

    if not is_valid:
        logger.debug("Invalid bearer token provided")
        return _unauthorized_request()

    # Authentication successful, proceed to next middleware/route
    logger.debug("Authentication successful")
    return next_middleware(app)


@app.post("/index-document", middlewares=[authentication_middleware])
@tracer.capture_method
def index_document():
    """Handle document indexing requests"""
    logger.info("Received document indexing request")

    # Get the request body
    request_body = app.current_event.body
    logger.debug("Request body received", extra={"body_length": len(request_body) if request_body else 0})

    # Add custom metric
    # metrics.add_metric(name="DocumentIndexRequests", unit=MetricUnit.Count, value=1)

    return Response(
        status_code=501,
        content_type=content_types.APPLICATION_JSON,
        body={"message": "Not yet implemented"}
    )


@app.exception_handler(Exception)
def handle_generic_exception(ex: Exception):
    """Handle any unhandled exceptions"""
    logger.exception("Unhandled exception occurred", extra={"error": str(ex)})
    metrics.add_metric(name="UnhandledExceptions", unit=MetricUnit.Count, value=1)

    return Response(
        status_code=500,
        content_type=content_types.APPLICATION_JSON,
        body={
            "error": "Internal server error",
            "message": "An unexpected error occurred"
        }
    )


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_REST)
@tracer.capture_lambda_handler
@metrics.log_metrics
def lambda_handler(event: Dict[str, Any], context: LambdaContext) -> Dict[str, Any]:
    """Lambda handler function"""
    logger.info("Lambda handler invoked", extra={"event_type": event.get("httpMethod", "unknown")})
    return app.resolve(event, context)
