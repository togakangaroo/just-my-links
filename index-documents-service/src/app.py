import json
import os
from typing import Dict, Any, List
from functools import cache

import boto3
from aws_lambda_powertools import Logger, Tracer, Metrics
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools.utilities.typing import LambdaContext

# Initialize powertools
logger = Logger(level=os.getenv("LOG_LEVEL", "INFO"))
tracer = Tracer()
metrics = Metrics(namespace="just-my-links")

# Initialize AWS clients
s3_client = boto3.client('s3')
eventbridge_client = boto3.client('events')


@tracer.capture_method
def process_sqs_record(record: Dict[str, Any]) -> None:
    """Process a single SQS record containing a document indexing event"""
    try:
        # Parse the SQS message body
        message_body = json.loads(record['body'])

        # Extract EventBridge event details
        event_detail = json.loads(message_body.get('detail', '{}'))
        folder_path = event_detail.get('folderPath')
        document_url = event_detail.get('documentUrl')

        logger.info("Processing document indexing event", extra={
            "folder_path": folder_path,
            "document_url": document_url,
            "message_id": record.get('messageId')
        })

        # For now, just log hello world
        logger.info("Hello world - document indexing placeholder")

        # TODO: Implement actual document indexing logic:
        # 1. Download ChromaDB from S3 to /tmp
        # 2. Read document files and metadata from S3
        # 3. Upsert document content array into ChromaDB
        # 4. Upload updated ChromaDB back to S3
        # 5. Publish "Document indexed" event

        metrics.add_metric(name="DocumentsProcessed", unit=MetricUnit.Count, value=1)

    except Exception as e:
        logger.error("Failed to process SQS record", extra={
            "error": str(e),
            "error_type": type(e).__name__,
            "record": record
        })
        metrics.add_metric(name="ProcessingErrors", unit=MetricUnit.Count, value=1)
        raise


@logger.inject_lambda_context(correlation_id_path=correlation_paths.SQS)
@tracer.capture_lambda_handler
@metrics.log_metrics
def lambda_handler(event: Dict[str, Any], context: LambdaContext) -> Dict[str, Any]:
    """Lambda handler function for SQS events"""
    try:
        logger.info("Lambda handler invoked", extra={
            "record_count": len(event.get('Records', [])),
            "function_name": context.function_name
        })

        # Process each SQS record
        for record in event.get('Records', []):
            process_sqs_record(record)

        logger.info("Successfully processed all SQS records")

        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Successfully processed SQS records",
                "processed_count": len(event.get('Records', []))
            })
        }

    except Exception as e:
        logger.exception("Unhandled exception in lambda_handler", extra={
            "error": str(e),
            "error_type": type(e).__name__,
            "event": event
        })
        metrics.add_metric(name="UnhandledExceptions", unit=MetricUnit.Count, value=1)

        # For SQS, we should raise the exception to trigger retry/DLQ behavior
        raise


@cache
def get_application_bucket() -> str:
    """Get application bucket name with caching"""
    application_bucket = os.getenv("APPLICATION_BUCKET")
    assert application_bucket, "APPLICATION_BUCKET environment variable not set"
    return application_bucket


@cache
def get_event_bus_name() -> str:
    """Get EventBridge event bus name with caching"""
    event_bus_name = os.getenv("EVENT_BUS_NAME")
    assert event_bus_name, "EVENT_BUS_NAME environment variable not set"
    return event_bus_name
