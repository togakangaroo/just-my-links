import base64
import hashlib
import io
import json
import os
import secrets
from contextlib import contextmanager
from functools import cache
from typing import Any, Dict, cast

import boto3
from python_multipart import MultipartParser
from aws_lambda_powertools import Logger, Metrics, Tracer
from aws_lambda_powertools.event_handler import (
    APIGatewayHttpResolver,
    Response,
    content_types,
)
from aws_lambda_powertools.event_handler.middlewares import NextMiddleware
from aws_lambda_powertools.logging import correlation_paths
from aws_lambda_powertools.metrics import MetricUnit
from aws_lambda_powertools.utilities.typing import LambdaContext
from python_multipart.multipart import parse_options_header

# Initialize powertools
logger = Logger(level=os.getenv("LOG_LEVEL", "INFO"))
tracer = Tracer()
metrics = Metrics(namespace="just-my-links")
app = APIGatewayHttpResolver()

# Initialize AWS clients
secrets_client = boto3.client('secretsmanager')
s3_client = boto3.client('s3')
eventbridge_client = boto3.client('events')

@app.put("/document")
@tracer.capture_method
def store_document():
    """Handle document storage requests"""
    # Get document URL from query parameter
    query_params = app.current_event.query_string_parameters or {}
    document_url = query_params.get('url')

    if not document_url:
        return Response(
            status_code=400,
            content_type=content_types.APPLICATION_JSON,
            body={"error": "Missing required 'url' query parameter"}
        )

    # TODO - rather than a comment, extract this to a well named method
    # Convert document_url to a safe S3 key using hash
    document_s3_path = hashlib.sha256(document_url.encode('utf-8')).hexdigest()
    logger.debug("Generated S3 path for document", extra={"document_url": document_url, "document_s3_path": document_s3_path})

    try:
        upload_results = _stream_multipart_to_s3(app.current_event, document_s3_path)
    except MultipartParsingError as e:
        return Response(
            status_code=e.status_code,
            content_type=content_types.APPLICATION_JSON,
            body={"error": e.message}
        )

    application_bucket, documents_folder = get_documents_folder()
    document_folder = f"{documents_folder}/{document_s3_path}"

    # Determine entrypoint file from uploaded files
    entrypoint = None
    uploaded_files = list(upload_results.keys())

    if 'document.html' in uploaded_files:
        entrypoint = 'document.html'
    elif 'document.txt' in uploaded_files:
        entrypoint = 'document.txt'
    else:
        return Response(
            status_code=400,
            content_type=content_types.APPLICATION_JSON,
            body={"error": "No document.html or document.txt file was successfully uploaded"}
        )

    with backup_in_case_of_error(application_bucket, document_folder):
        # Create metadata.json
        metadata = {
            "documentUrl": document_url,
            "entrypoint": entrypoint,
            "files": uploaded_files,
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
            "files": uploaded_files
        }
    )


class MultipartParsingError(Exception):
    """Custom exception for multipart parsing errors with status codes"""
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class StreamingS3Upload:
    """Handles streaming upload to S3 with size limits using multipart upload"""

    def __init__(self, s3_client, bucket: str, key: str, content_type: str, max_size: int = 2 * 1024 * 1024):
        self.s3_client = s3_client
        self.bucket = bucket
        self.key = key
        self.content_type = content_type
        self.max_size = max_size
        self.current_size = 0
        self.size_exceeded = False
        self.completed = False
        self.aborted = False

        # S3 multipart upload state
        self.upload_id = None
        self.parts = []
        self.part_number = 1
        self.current_part_buffer = io.BytesIO()
        self.min_part_size = 5 * 1024 * 1024  # 5MB minimum for multipart (except last part)

        # For small files, we'll use regular put_object
        self.use_multipart = False

    def _start_multipart_upload(self):
        """Initialize multipart upload"""
        if self.upload_id is None:
            response = self.s3_client.create_multipart_upload(
                Bucket=self.bucket,
                Key=self.key,
                ContentType=self.content_type
            )
            self.upload_id = response['UploadId']
            self.use_multipart = True

    def _upload_part_if_ready(self, force=False):
        """Upload a part if buffer is large enough or if forced"""
        buffer_size = self.current_part_buffer.tell()

        if not force and buffer_size < self.min_part_size:
            return

        if buffer_size == 0:
            return

        self._start_multipart_upload()

        self.current_part_buffer.seek(0)
        response = self.s3_client.upload_part(
            Bucket=self.bucket,
            Key=self.key,
            PartNumber=self.part_number,
            UploadId=self.upload_id,
            Body=self.current_part_buffer.getvalue()
        )

        self.parts.append({
            'ETag': response['ETag'],
            'PartNumber': self.part_number
        })

        self.part_number += 1
        self.current_part_buffer = io.BytesIO()

    def write(self, data: bytes) -> None:
        """Write data to the stream, checking size limits"""
        if self.size_exceeded or self.aborted:
            return

        if self.current_size + len(data) > self.max_size:
            self.size_exceeded = True
            self._abort_upload()
            return

        self.current_size += len(data)
        self.current_part_buffer.write(data)

        # Upload part if buffer is large enough
        self._upload_part_if_ready()

    def _abort_upload(self):
        """Abort the multipart upload"""
        if self.upload_id and not self.aborted:
            try:
                self.s3_client.abort_multipart_upload(
                    Bucket=self.bucket,
                    Key=self.key,
                    UploadId=self.upload_id
                )
            except Exception as e:
                logger.warning("Failed to abort multipart upload", extra={"error": str(e)})
            self.aborted = True

    def complete(self) -> bool:
        """Complete the upload. Returns True if successful, False if size exceeded"""
        if self.size_exceeded:
            return False

        if self.current_size == 0:
            return True

        try:
            if self.use_multipart:
                # Upload final part
                self._upload_part_if_ready(force=True)

                # Complete multipart upload
                self.s3_client.complete_multipart_upload(
                    Bucket=self.bucket,
                    Key=self.key,
                    UploadId=self.upload_id,
                    MultipartUpload={'Parts': self.parts}
                )
            else:
                # For small files, use regular put_object
                self.current_part_buffer.seek(0)
                self.s3_client.put_object(
                    Bucket=self.bucket,
                    Key=self.key,
                    Body=self.current_part_buffer.getvalue(),
                    ContentType=self.content_type
                )

            self.completed = True
            return True

        except Exception as e:
            logger.error("Failed to complete S3 upload", extra={"error": str(e)})
            self._abort_upload()
            return False

    def get_size(self) -> int:
        """Get the current size of uploaded data"""
        return self.current_size


def _is_acceptable_content_type(content_type: str) -> bool:
    """Check if the content type is acceptable for document storage"""
    acceptable_types = [
        'text/plain',
        'text/html',
        'application/octet-stream',  # Allow this as it's often used as default
        ''  # Allow empty content type
    ]

    # Extract main content type (ignore charset and other parameters)
    main_type = content_type.split(';')[0].strip()
    return main_type in acceptable_types



def _stream_multipart_to_s3(event, document_s3_path: str) -> Dict[str, int]:
    """Stream multipart request body directly to S3 with size limits

    Args:
        event: API Gateway event object
        document_url: The document URL for logging
        document_s3_path: The S3 path prefix for this document

    Returns:
        Dict[str, int]: Dictionary mapping successfully uploaded filenames to their sizes

    Raises:
        MultipartParsingError: If parsing fails or document part is missing/too large
    """
    application_bucket, documents_folder = get_documents_folder()
    document_folder = f"{documents_folder}/{document_s3_path}"

    MAX_FILE_SIZE = 2 * 1024 * 1024  # 2MB
    request_body = event.body
    content_type_header = getattr(event, 'headers', {}).get('content-type', '')
    content_type, options = parse_options_header(content_type_header)

    if not request_body or not content_type.decode('latin-1').startswith('multipart/form-data'):
        logger.error("Request is not multipart/form-data", extra={"content_type": content_type})
        raise MultipartParsingError("Request must be multipart/form-data")

    # Decode base64 if needed
    is_base64_encoded = event.get('isBase64Encoded', False)
    if is_base64_encoded:
        try:
            logger.debug("base64 decoding")
            request_body = base64.b64decode(request_body)
        except Exception as e:
            logger.error("Failed to decode base64 request body", extra={"error": str(e)})
            raise MultipartParsingError("Failed to decode base64 request body")
    elif isinstance(request_body, str):
        request_body = request_body.encode('utf-8')

    boundary = options.get(b'boundary')
    if not boundary:
        raise MultipartParsingError("No boundary found in Content-Type header")

    # Parse multipart data using callback-based approach with streaming to S3
    uploaded_files = {}
    current_part_name = None
    current_filename = None
    current_upload = None
    current_headers = {}
    header_name_buffer = []
    header_value_buffer = []
    document_part_found = False
    document_part_too_large = False

    def on_part_begin():
        nonlocal current_part_name, current_filename, current_upload, current_headers
        current_part_name = None
        current_filename = None
        current_upload = None
        current_headers = {}

    def on_part_data(data, start, end):
        if current_upload:
            current_upload.write(data[start:end])

    def on_part_end():
        nonlocal document_part_found, document_part_too_large
        if not current_upload:
            return
        success = current_upload.complete()
        if success:
            uploaded_files[current_filename] = current_upload.get_size()
            logger.debug("Successfully uploaded file", extra={
                "file_name": current_filename,
                "size": current_upload.get_size()
            })
        else:
            logger.warning("File exceeded size limit", extra={
                "file_name": current_part_name,
                "max_size": MAX_FILE_SIZE
            })
            # Check if this was the document part
            if current_part_name == 'document':
                document_part_too_large = True

    def on_header_field(data, start, end):
        header_name_buffer.append(data[start:end])

    def on_header_value(data, start, end):
        header_value_buffer.append(data[start:end])

    def on_header_end():
        header_name = b''.join(header_name_buffer).decode('utf-8').lower()
        header_value = b''.join(header_value_buffer).decode('utf-8')
        current_headers[header_name] = header_value

        # Clear buffers for next header
        header_name_buffer.clear()
        header_value_buffer.clear()

    def on_headers_finished():
        nonlocal current_part_name, current_filename, current_upload, document_part_found
        # Parse Content-Disposition header to get field name
        content_disp = current_headers.get('content-disposition', '')
        if 'name=' in content_disp:
            # Extract name from Content-Disposition header
            import re
            match = re.search(r'name=(?:"([^"]+)"|([^;\s]+))', content_disp)
            if match:
                current_part_name = match.group(1) or match.group(2)

                # Track if we found the document part
                if current_part_name == 'document':
                    document_part_found = True

                # Validate content type for document parts
                if current_part_name == 'document':
                    content_type = current_headers.get('content-type', '').lower()
                    if content_type and not _is_acceptable_content_type(content_type):
                        raise MultipartParsingError(
                            f"Document part has unsupported content type: {content_type}. "
                            "Only text/plain and text/html are supported."
                        )

                # Determine filename and content type for S3
                if current_part_name == 'document':
                    # Determine if HTML or text based on content type or simple heuristic
                    content_type = current_headers.get('content-type', '').lower()
                    if 'html' in content_type:
                        filename = 'document.html'
                        s3_content_type = 'text/html'
                    else:
                        filename = 'document.txt'
                        s3_content_type = 'text/plain'
                else:
                    filename = current_part_name
                    if filename.endswith('.html'):
                        s3_content_type = 'text/html'
                    else:
                        s3_content_type = 'text/plain'

                # Create streaming upload
                current_filename = filename
                file_key = f"{document_folder}/{filename}"
                current_upload = StreamingS3Upload(
                    s3_client=s3_client,
                    bucket=application_bucket,
                    key=file_key,
                    content_type=s3_content_type,
                    max_size=MAX_FILE_SIZE
                )

    # Set up callbacks
    callbacks = {
        'on_part_begin': on_part_begin,
        'on_part_data': on_part_data,
        'on_part_end': on_part_end,
        'on_header_field': on_header_field,
        'on_header_value': on_header_value,
        'on_header_end': on_header_end,
        'on_headers_finished': on_headers_finished,
    }

    # Create parser and feed it data
    parser = MultipartParser(boundary, cast(Any, callbacks)) # Note the cast is the easiest way to bypass a complex typing mechanic. You can't just import the underlying type as it doesn't exist during runtime
    parser.write(request_body)

    # Check if document part was found and handle size errors
    if not document_part_found:
        logger.error("No 'document' part found in multipart form-data")
        raise MultipartParsingError("Missing required 'document' part")

    if document_part_too_large:
        logger.error("Document part exceeded size limit", extra={"max_size": MAX_FILE_SIZE})
        raise MultipartParsingError(
            f"Document file is too large. Maximum allowed size is {MAX_FILE_SIZE // (1024*1024)}MB."
        )

    logger.debug("Multipart parts streamed to S3", extra={
        "file_count": len(uploaded_files),
        "file_names": list(uploaded_files.keys())
    })

    return uploaded_files


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
            logger.debug("Document folder exists, creating backup", extra={"folder": document_folder})

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

        # TODO - refactor this and the similar folder dletion further down into a helper function
        # Delete existing folder contents
        if folder_exists:
            logger.debug("Deleting existing document folder", extra={"folder": document_folder})
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
            logger.debug("Restoring from backup due to error")
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

@logger.inject_lambda_context(correlation_id_path=correlation_paths.API_GATEWAY_HTTP)
@tracer.capture_lambda_handler
@metrics.log_metrics
def lambda_handler(event: Dict[str, Any], context: LambdaContext) -> Dict[str, Any]:
    """Lambda handler function"""
    try:
        logger.debug("Lambda handler invoked", extra={
            "event_type": event.get("httpMethod", "unknown"),
            "path": event.get("path", "unknown"),
            "resource": event.get("resource", "unknown"),
            "request_context": event.get("requestContext", {})
        })

        result = app.resolve(event, context)
        logger.info("Request resolved", extra={"status_code": result.get("statusCode")})
        return result
    except Exception as e:
        logger.exception("Unhandled exception in lambda_handler", extra={
            "error": str(e),
            "error_type": type(e).__name__,
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
