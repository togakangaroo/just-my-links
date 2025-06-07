import json
import os
import secrets
import hashlib
from functools import cache
from typing import Dict, Any
import base64
from contextlib import contextmanager
import io

import boto3
from multipart import parse_options_header, MultipartParser
from aws_lambda_powertools import Logger, Tracer, Metrics
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools.event_handler import APIGatewayHttpResolver, Response, content_types
from aws_lambda_powertools.event_handler.middlewares import NextMiddleware
from aws_lambda_powertools.utilities.typing import LambdaContext

# Initialize powertools
logger = Logger(level=os.getenv("LOG_LEVEL", "INFO"))
tracer = Tracer()
metrics = Metrics(namespace="just-my-links")
app = APIGatewayHttpResolver()

# Initialize AWS clients
secrets_client = boto3.client('secretsmanager')
s3_client = boto3.client('s3')
eventbridge_client = boto3.client('events')


class MultipartParsingError(Exception):
    """Custom exception for multipart parsing errors with status codes"""
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _get_multipart_request_body(event) -> Dict[str, bytes]:
    """Extract and parse multipart request body from API Gateway event
    
    Args:
        event: API Gateway event object
        
    Returns:
        Dict[str, bytes]: Dictionary mapping part names to their content
        
    Raises:
        MultipartParsingError: If parsing fails or document part is missing
    """
    request_body = event.body
    headers = getattr(event, 'headers', {})
    content_type = headers.get('content-type', '')

    if not request_body or 'multipart/form-data' not in content_type:
        logger.error("Request is not multipart/form-data", extra={"content_type": content_type})
        raise MultipartParsingError("Request must be multipart/form-data")

    # Decode base64 if needed
    is_base64_encoded = event.get('isBase64Encoded', False)
    if is_base64_encoded:
        try:
            request_body = base64.b64decode(request_body)
        except Exception as e:
            logger.error("Failed to decode base64 request body", extra={"error": str(e)})
            raise MultipartParsingError("Failed to decode base64 request body")
    elif isinstance(request_body, str):
        request_body = request_body.encode('utf-8')

    # Parse content type to get boundary
    try:
        content_type_header, options = parse_options_header(content_type)
        boundary = options.get('boundary')
        if not boundary:
            raise MultipartParsingError("No boundary found in Content-Type header")
    except Exception as e:
        logger.error("Failed to parse Content-Type header", extra={"error": str(e)})
        raise MultipartParsingError("Invalid Content-Type header")

    # Parse multipart data
    try:
        parser = MultipartParser(boundary.encode())
        parts = parser.parse(io.BytesIO(request_body))
        
        # Extract all parts
        parsed_parts = {}
        document_found = False
        
        for part in parts:
            if part.name:
                parsed_parts[part.name] = part.raw
                if part.name == 'document':
                    document_found = True
        
        if not document_found:
            logger.error("No 'document' part found in multipart form-data")
            raise MultipartParsingError("Missing required 'document' part")
            
        logger.info("Multipart parts parsed", extra={
            "part_count": len(parsed_parts),
            "part_names": list(parsed_parts.keys())
        })
        
        return parsed_parts
        
    except MultipartParsingError:
        raise
    except Exception as e:
        logger.error("Failed to parse multipart form-data", extra={"error": str(e)})
        raise MultipartParsingError("Failed to parse multipart form-data")


@app.put("/document/<document_url>")
@tracer.capture_method
def store_document(document_url: str):
    """Handle document storage requests"""
    # Convert document_url to a safe S3 key using hash
    document_s3_path = hashlib.sha256(document_url.encode('utf-8')).hexdigest()
    logger.debug("Generated S3 path for document", extra={"document_url": document_url, "document_s3_path": document_s3_path})

    try:
        multipart_parts = _get_multipart_request_body(app.current_event)
    except MultipartParsingError as e:
        return Response(
            status_code=e.status_code,
            content_type=content_types.APPLICATION_JSON,
            body={"error": e.message}
        )

    application_bucket, documents_folder = get_documents_folder()
    document_folder = f"{documents_folder}/{document_s3_path}"

    # Determine entrypoint file
    entrypoint = None
    if 'document.html' in multipart_parts:
        entrypoint = 'document.html'
    elif 'document.txt' in multipart_parts:
        entrypoint = 'document.txt'
    elif 'document' in multipart_parts:
        # If just 'document' part, determine type and save as appropriate file
        document_content = multipart_parts['document']
        # Simple heuristic: if it contains HTML tags, treat as HTML
        if b'<html' in document_content.lower() or b'<body' in document_content.lower():
            entrypoint = 'document.html'
            multipart_parts['document.html'] = document_content
            del multipart_parts['document']
        else:
            entrypoint = 'document.txt'
            multipart_parts['document.txt'] = document_content
            del multipart_parts['document']
    else:
        return Response(
            status_code=400,
            content_type=content_types.APPLICATION_JSON,
            body={"error": "No document.html, document.txt, or document part found"}
        )

    with backup_in_case_of_error(application_bucket, document_folder):
        # Store all multipart files in S3
        for filename, content in multipart_parts.items():
            file_key = f"{document_folder}/{filename}"
            
            # Determine content type
            content_type = 'text/plain'
            if filename.endswith('.html'):
                content_type = 'text/html'
            elif filename.endswith('.txt'):
                content_type = 'text/plain'
            
            s3_client.put_object(
                Bucket=application_bucket,
                Key=file_key,
                Body=content,
                ContentType=content_type
            )
            logger.debug("Stored file in S3", extra={"key": file_key, "size": len(content)})

        # Create metadata.json
        metadata = {
            "documentUrl": document_url,
            "entrypoint": entrypoint,
            "files": list(multipart_parts.keys()),
            "timestamp": json.dumps({"$date": {"$numberLong": str(int(__import__('time').time() * 1000))}})
        }
        
        metadata_key = f"{document_folder}/.metadata.json"
        s3_client.put_object(
            Bucket=application_bucket,
            Key=metadata_key,
            Body=json.dumps(metadata, indent=2),
            ContentType='application/json'
        )
        logger.info("Stored metadata", extra={"metadata_key": metadata_key, "entrypoint": entrypoint})

        # Publish event to EventBridge
        try:
            event_bus_name = get_event_bus_name()
            event_detail = {
                "folderPath": document_folder,
                "documentUrl": document_url
            }
            
            eventbridge_client.put_events(
                Entries=[
                    {
                        'Source': 'just-my-links.document-storage',
                        'DetailType': 'Document stored',
                        'Detail': json.dumps(event_detail),
                        'EventBusName': event_bus_name
                    }
                ]
            )
            logger.info("Published event to EventBridge", extra={"event_detail": event_detail})
            
        except Exception as e:
            logger.error("Failed to publish event to EventBridge", extra={"error": str(e)})
            # Don't fail the request if event publishing fails
            
    return Response(
        status_code=200,
        content_type=content_types.APPLICATION_JSON,
        body={
            "message": "Document stored successfully",
            "folderPath": document_folder,
            "entrypoint": entrypoint,
            "files": list(multipart_parts.keys())
        }
    )



@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_REST)
@tracer.capture_lambda_handler
@metrics.log_metrics
def lambda_handler(event: Dict[str, Any], context: LambdaContext) -> Dict[str, Any]:
    """Lambda handler function"""
    try:
        logger.info("Lambda handler invoked", extra={"event_type": event.get("httpMethod", "unknown")})
        return app.resolve(event, context)
    except Exception as e:
        logger.exception("Unhandled exception in lambda_handler", extra={
            "error": str(e),
            "error_type": type(e).__name__,
            "event": event
        })
        metrics.add_metric(name="UnhandledExceptions", unit=MetricUnit.Count, value=1)

        # Return a proper API Gateway response
        return {
            "statusCode": 500,
            "headers": {
                "Content-Type": "application/json"
            },
            "body": json.dumps({
                "error": "Internal server error",
                "message": "An unexpected error occurred"
            })
        }

@cache
def get_bearer_token() -> str:
    """Get bearer token from Secrets Manager with caching"""
    secret_arn = os.getenv("BEARER_TOKEN_SECRET_ARN")
    logger.debug("Will fetch token from Secrets Manager", extra={"secret_arn":secret_arn})

    if not secret_arn:
        logger.error("BEARER_TOKEN_SECRET_ARN environment variable not set")
        raise ValueError("BEARER_TOKEN_SECRET_ARN environment variable not set")

    try:
        response = secrets_client.get_secret_value(SecretId=secret_arn)
        logger.debug("Bearer token retrieved from Secrets Manager")
        return response['SecretString']
    except Exception as e:
        logger.error("Failed to retrieve bearer token", extra={"error": str(e), "secret_arn": secret_arn})
        raise


@cache
def get_documents_folder() -> tuple[str, str]:
    """Get application bucket name and documents folder with caching"""
    application_bucket = os.getenv("APPLICATION_BUCKET")
    assert application_bucket, "APPLICATION_BUCKET environment variable not set"
    documents_folder = "document-storage"
    return application_bucket, documents_folder


@cache
def get_event_bus_name() -> str:
    """Get EventBridge event bus name with caching"""
    event_bus_name = os.getenv("EVENT_BUS_NAME")
    assert event_bus_name, "EVENT_BUS_NAME environment variable not set"
    return event_bus_name


@contextmanager
def backup_in_case_of_error(bucket: str, document_folder: str):
    """Context manager to handle backup/restore logic for S3 folder operations"""
    backup_folder = f"{document_folder}.bak"
    folder_exists = False
    backup_created = False
    
    try:
        # Check if the folder exists by listing objects with the prefix
        response = s3_client.list_objects_v2(
            Bucket=bucket,
            Prefix=f"{document_folder}/",
            MaxKeys=1
        )
        folder_exists = response.get('KeyCount', 0) > 0

        if folder_exists:
            logger.info("Document folder exists, creating backup", extra={"folder": document_folder})

            # Create backup by copying all objects
            paginator = s3_client.get_paginator('list_objects_v2')
            for page in paginator.paginate(Bucket=bucket, Prefix=f"{document_folder}/"):
                for obj in page.get('Contents', []):
                    old_key = obj['Key']
                    new_key = old_key.replace(f"{document_folder}/", f"{backup_folder}/", 1)

                    s3_client.copy_object(
                        Bucket=bucket,
                        CopySource={'Bucket': bucket, 'Key': old_key},
                        Key=new_key
                    )

            backup_created = True
            logger.debug("Backup created successfully", extra={"backup_folder": backup_folder})

        # Delete existing folder contents
        if folder_exists:
            logger.info("Deleting existing document folder", extra={"folder": document_folder})
            paginator = s3_client.get_paginator('list_objects_v2')
            for page in paginator.paginate(Bucket=bucket, Prefix=f"{document_folder}/"):
                objects_to_delete = [{'Key': obj['Key']} for obj in page.get('Contents', [])]
                if objects_to_delete:
                    s3_client.delete_objects(
                        Bucket=bucket,
                        Delete={'Objects': objects_to_delete}
                    )

        # Yield control to the calling code
        yield

    except Exception as e:
        logger.error("Error during folder operations", extra={"error": str(e)})

        # Restore from backup if it was created
        if backup_created:
            logger.info("Restoring from backup due to error")
            try:
                # Delete any partial changes
                paginator = s3_client.get_paginator('list_objects_v2')
                for page in paginator.paginate(Bucket=bucket, Prefix=f"{document_folder}/"):
                    objects_to_delete = [{'Key': obj['Key']} for obj in page.get('Contents', [])]
                    if objects_to_delete:
                        s3_client.delete_objects(
                            Bucket=bucket,
                            Delete={'Objects': objects_to_delete}
                        )

                # Restore from backup
                for page in paginator.paginate(Bucket=bucket, Prefix=f"{backup_folder}/"):
                    for obj in page.get('Contents', []):
                        old_key = obj['Key']
                        new_key = old_key.replace(f"{backup_folder}/", f"{document_folder}/", 1)

                        s3_client.copy_object(
                            Bucket=bucket,
                            CopySource={'Bucket': bucket, 'Key': old_key},
                            Key=new_key
                        )

                logger.info("Backup restored successfully")
            except Exception as restore_error:
                logger.error("Failed to restore backup", extra={"error": str(restore_error)})

        raise  # Re-raise the original exception

    finally:
        # Clean up backup if it was created
        if backup_created:
            try:
                logger.debug("Cleaning up backup folder", extra={"backup_folder": backup_folder})
                paginator = s3_client.get_paginator('list_objects_v2')
                for page in paginator.paginate(Bucket=bucket, Prefix=f"{backup_folder}/"):
                    objects_to_delete = [{'Key': obj['Key']} for obj in page.get('Contents', [])]
                    if objects_to_delete:
                        s3_client.delete_objects(
                            Bucket=bucket,
                            Delete={'Objects': objects_to_delete}
                        )
                logger.debug("Backup cleanup completed")
            except Exception as cleanup_error:
                logger.warning("Failed to clean up backup", extra={"error": str(cleanup_error)})

def _unauthorized_request() -> Response:
    metrics.add_metric(name="UnauthorizedRequests", unit=MetricUnit.Count, value=1)
    return Response(
        status_code=401,
        content_type=content_types.APPLICATION_JSON,
        body={"error": "Unauthorized"})


def authentication_middleware(app: APIGatewayHttpResolver, next_middleware: NextMiddleware) -> Response:
    """Middleware to authenticate requests using bearer token"""

    headers = getattr(app.current_event, 'headers', None) or {}
    auth_header = headers.get("Authorization", "")

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


# Register global middleware
app.use(middlewares=[authentication_middleware])

