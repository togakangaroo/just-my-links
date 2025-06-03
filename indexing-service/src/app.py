import json
import os
import secrets
from functools import cache
from typing import Dict, Any

import boto3
from aws_lambda_powertools import Logger, Tracer, Metrics
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools.event_handler import APIGatewayRestResolver
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
    environment_name = os.getenv("ENVIRONMENT_NAME", "dev")
    secret_name = f"{environment_name}-just-my-links-bearer-token"

    try:
        response = secrets_client.get_secret_value(SecretId=secret_name)
        logger.debug("Bearer token retrieved from Secrets Manager")
        return response['SecretString']
    except Exception as e:
        logger.error("Failed to retrieve bearer token", extra={"error": str(e)})
        raise


def authenticate_request() -> bool:
    """Authenticate the request using bearer token"""
    auth_header = app.current_event.headers.get("Authorization", "")

    if not auth_header.startswith("Bearer "):
        logger.warning("Missing or invalid Authorization header format")
        return False

    provided_token = auth_header[7:]  # Remove "Bearer " prefix
    expected_token = get_bearer_token()

    # Use secrets.compare_digest for safe string comparison
    is_valid = secrets.compare_digest(provided_token, expected_token)

    if not is_valid:
        logger.warning("Invalid bearer token provided")
        metrics.add_metric(name="AuthenticationFailures", unit=MetricUnit.Count, value=1)

    return is_valid


@app.post("/index-document")
@tracer.capture_method
def index_document():
    """Handle document indexing requests"""
    logger.info("Received document indexing request")

    # Authenticate the request
    if not authenticate_request():
        metrics.add_metric(name="UnauthorizedRequests", unit=MetricUnit.Count, value=1)
        return {
            "statusCode": 401,
            "body": json.dumps({"error": "Unauthorized"})
        }

    # Get the request body
    request_body = app.current_event.body
    logger.debug("Request body received", extra={"body_length": len(request_body) if request_body else 0})

    # Add custom metric
    metrics.add_metric(name="DocumentIndexRequests", unit=MetricUnit.Count, value=1)

    response = {
        "message": "Not yet implemented",
        "statusCode": 501,
    }

    return response


@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_REST)
@tracer.capture_lambda_handler
@metrics.log_metrics
def lambda_handler(event: Dict[str, Any], context: LambdaContext) -> Dict[str, Any]:
    """Lambda handler function"""
    logger.info("Lambda handler invoked", extra={"event_type": event.get("httpMethod", "unknown")})

    try:
        return app.resolve(event, context)
    except Exception as e:
        logger.exception("Error processing request")
        return {
            "statusCode": 500,
            "body": json.dumps({
                "error": "Internal server error",
                "message": str(e)
            })
        }
