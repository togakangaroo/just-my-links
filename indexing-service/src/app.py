import json
import os
from typing import Dict, Any

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


@app.post("/index-document")
@tracer.capture_method
def index_document():
    """Handle document indexing requests"""
    logger.info("Received document indexing request")

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
