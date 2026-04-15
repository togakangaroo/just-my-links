import base64
import hashlib
import io
import json
import os
import re
import secrets
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import cache
from typing import Any, Dict, List, Optional, cast

from aws_lambda_powertools.utilities.data_classes import APIGatewayProxyEventV2
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
ssm_client = boto3.client("ssm")
s3_client = boto3.client("s3")
eventbridge_client = boto3.client("events")


def _to_s3_key(document_url: str) -> str:
    return hashlib.sha256(document_url.encode("utf-8")).hexdigest()


@app.put("/document")
@tracer.capture_method
def store_document():
    """Handle document storage requests"""
    # Get document URL from query parameter
    query_params = app.current_event.query_string_parameters or {}
    document_url = query_params.get("url")
    document_title = query_params.get("title") or None

    if not document_url:
        return Response(
            status_code=400,
            content_type=content_types.APPLICATION_JSON,
            body={"error": "Missing required 'url' query parameter"},
        )

    document_s3_path = _to_s3_key(document_url)

    application_bucket, documents_folder = get_documents_folder()
    document_folder = f"{documents_folder}/{document_s3_path}"

    with backup_in_case_of_error(application_bucket, document_folder):
        try:
            uploaded_files = list(
                _stream_multipart_to_s3(app.current_event, document_s3_path).keys()
            )
        except MultipartParsingError as e:
            return Response(
                status_code=e.status_code,
                content_type=content_types.APPLICATION_JSON,
                body={"error": e.message},
            )

        allowed_entrypoints = ("document.html", "document.txt", "document.pdf")
        entrypoint = next((x for x in uploaded_files if x in allowed_entrypoints), None)
        if not entrypoint:
            return Response(
                status_code=400,
                content_type=content_types.APPLICATION_JSON,
                body={
                    "error": "No document.html, document.txt, or document.pdf file was successfully uploaded"
                },
            )
        metadata = {
            "documentUrl": document_url,
            "entrypoint": entrypoint,
            "files": uploaded_files,
            "timestamp": json.dumps(
                {"$date": {"$numberLong": str(int(__import__("time").time() * 1000))}}
            ),
        }
        if document_title:
            metadata["title"] = document_title

        s3_client.put_object(
            Bucket=application_bucket,
            Key=f"{document_folder}/.metadata.json",
            Body=json.dumps(metadata, indent=2),
            ContentType="application/json",
        )
        logger.info(
            "Stored document",
            extra={"s3_path": document_folder, "document_url": document_url},
        )

        event_bus_name = get_event_bus_name()
        event_detail = {"folderPath": document_folder, "documentUrl": document_url}

        eventbridge_client.put_events(
            Entries=[
                {
                    "Source": "just-my-links.document-storage",
                    "DetailType": "Document stored",
                    "Detail": json.dumps(event_detail),
                    "EventBusName": event_bus_name,
                }
            ]
        )
        logger.debug(
            "Published event to EventBridge", extra={"event_detail": event_detail}
        )

    return Response(
        status_code=200,
        content_type=content_types.APPLICATION_JSON,
        body={"message": "Document stored successfully", "files": uploaded_files},
    )


class MultipartParsingError(Exception):
    """Custom exception for multipart parsing errors with status codes"""

    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


@dataclass
class StreamingS3Upload:
    """Handles streaming upload to S3 with size limits using multipart upload"""

    s3_client: Any
    bucket: str
    key: str
    content_type: str
    max_size: int = 2 * 1024 * 1024
    current_size: int = field(default=0, init=False)
    size_exceeded: bool = field(default=False, init=False)
    completed: bool = field(default=False, init=False)
    aborted: bool = field(default=False, init=False)

    # S3 multipart upload state
    upload_id: Optional[str] = field(default=None, init=False)
    parts: List[Dict[str, Any]] = field(default_factory=list, init=False)
    part_number: int = field(default=1, init=False)
    current_part_buffer: io.BytesIO = field(default_factory=io.BytesIO, init=False)
    min_part_size: int = field(
        default=5 * 1024 * 1024, init=False
    )  # 5MB minimum for multipart (except last part)

    # For small files, we'll use regular put_object
    use_multipart: bool = field(default=False, init=False)

    def get_filename(self) -> str:
        """Get the filename from the S3 key"""
        return self.key.split("/")[-1]

    def _start_multipart_upload(self) -> None:
        """Initialize multipart upload"""
        if self.upload_id is None:
            response = self.s3_client.create_multipart_upload(
                Bucket=self.bucket, Key=self.key, ContentType=self.content_type
            )
            self.upload_id = response["UploadId"]
            self.use_multipart = True

    def _upload_part_if_ready(self, force: bool = False) -> None:
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
            Body=self.current_part_buffer.getvalue(),
        )

        self.parts.append({"ETag": response["ETag"], "PartNumber": self.part_number})

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

    def _abort_upload(self) -> None:
        """Abort the multipart upload"""
        if self.upload_id and not self.aborted:
            try:
                self.s3_client.abort_multipart_upload(
                    Bucket=self.bucket, Key=self.key, UploadId=self.upload_id
                )
            except Exception as e:
                logger.warning(
                    "Failed to abort multipart upload", extra={"error": str(e)}
                )
            self.aborted = True

    def complete(self) -> bool:
        """Complete the upload. Returns True if successful, False if size exceeded"""
        if self.size_exceeded:
            return False

        if self.current_size == 0:
            return True

        try:
            if self.use_multipart:
                self._upload_part_if_ready(force=True)

                self.s3_client.complete_multipart_upload(
                    Bucket=self.bucket,
                    Key=self.key,
                    UploadId=self.upload_id,
                    MultipartUpload={"Parts": self.parts},
                )
            else:
                self.current_part_buffer.seek(0)
                self.s3_client.put_object(
                    Bucket=self.bucket,
                    Key=self.key,
                    Body=self.current_part_buffer.getvalue(),
                    ContentType=self.content_type,
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


def _ensure_document_headers_are_valid(
    current_part_name: str | None, content_type: str
):
    if current_part_name != "document":
        return

    acceptable_types = [
        "text/plain",
        "text/html",
        "application/pdf",
    ]

    if content_type not in acceptable_types:
        raise MultipartParsingError(
            f"Document upload part has unsupported content type: {content_type}"
        )


def _get_content_disposition_field(
    field_name: str, content_disposition: str
) -> str | None:
    match = re.search(
        r"" + field_name + r'=(?:"([^"]+)"|([^;\s]+))', content_disposition
    )
    if not match:
        logger.debug(
            f"Content-Disposition field {field_name} not found. ",
            extra={
                "content_disposition": content_disposition,
                "field_name": field_name,
            },
        )
        return None
    return match.group(1) or match.group(2)


def _stream_multipart_to_s3(
    event: APIGatewayProxyEventV2, s3_folder_name: str
) -> Dict[str, int]:
    """Stream multipart request body directly to S3 with size limits"""
    application_bucket, documents_folder = get_documents_folder()
    document_folder = f"{documents_folder}/{s3_folder_name}"

    MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB
    request_body = event.body
    content_type_header = getattr(event, "headers", {}).get("content-type", "")
    content_type, options = parse_options_header(content_type_header)

    if not request_body or not content_type.decode("latin-1").startswith(
        "multipart/form-data"
    ):
        logger.error(
            "Request must have a body and be multipart/form-data",
            extra={"content_type": content_type},
        )
        raise MultipartParsingError("Request must be multipart/form-data")

    boundary = options.get(b"boundary")
    if not boundary:
        raise MultipartParsingError("No boundary found in Content-Type header")

    # Note tht API Gateway may (will?) base64 encode the request body
    is_base64_encoded = event.get("isBase64Encoded", False)
    if is_base64_encoded:
        logger.debug("base64 decoding")
        request_body = base64.b64decode(request_body)
    elif isinstance(request_body, str):
        request_body = request_body.encode("utf-8")

    # Parse multipart data using callback-based approach with streaming to S3
    uploaded_files = {}
    current_part_name: str | None = None
    current_upload: StreamingS3Upload | None = None
    current_headers: dict[str, str] = {}
    header_name_buffer: list[bytes] = []
    header_value_buffer: list[bytes] = []
    document_part_too_large: bool = False

    def on_part_begin():
        nonlocal current_part_name, current_upload, current_headers
        current_part_name = None
        current_upload = None
        current_headers = {}

    def on_part_data(data: bytes, start: int, end: int):
        if current_upload:
            current_upload.write(data[start:end])

    def on_part_end():
        nonlocal document_part_too_large
        if not current_upload:
            return
        success = current_upload.complete()
        if success:
            # Store the actual filename used in S3, not the form field name
            actual_filename = current_upload.get_filename()
            uploaded_files[actual_filename] = current_upload.get_size()
            logger.debug(
                "Successfully uploaded file",
                extra={
                    "form_field_name": current_part_name,
                    "actual_filename": actual_filename,
                    "size": current_upload.get_size(),
                },
            )
        else:
            logger.warning(
                "File exceeded size limit",
                extra={"file_name": current_part_name, "max_size": MAX_FILE_SIZE},
            )
            # Check if this was the document part
            if current_part_name == "document":
                document_part_too_large = True

    def on_header_field(data: bytes, start: int, end: int):
        header_name_buffer.append(data[start:end])

    def on_header_value(data: bytes, start: int, end: int):
        header_value_buffer.append(data[start:end])

    def on_header_end():
        header_name = b"".join(header_name_buffer).decode("utf-8").lower()
        header_value = b"".join(header_value_buffer).decode("utf-8")
        current_headers[header_name] = header_value

        header_name_buffer.clear()
        header_value_buffer.clear()

    def on_headers_finished():
        nonlocal current_part_name, current_upload
        # Parse Content-Disposition header to get field name
        content_disposition = current_headers.get("content-disposition", "")

        current_part_name = _get_content_disposition_field("name", content_disposition)

        content_type = current_headers.get("content-type", "").lower()

        _ensure_document_headers_are_valid(current_part_name, content_type)

        filename = (
            _get_content_disposition_field("filename", content_disposition)
            or f"{current_part_name}.txt"
        )

        current_upload = StreamingS3Upload(
            s3_client=s3_client,
            bucket=application_bucket,
            key=f"{document_folder}/{filename}",
            content_type=content_type,
            max_size=MAX_FILE_SIZE,
        )

    # Set up callbacks
    callbacks = {
        "on_part_begin": on_part_begin,
        "on_part_data": on_part_data,
        "on_part_end": on_part_end,
        "on_header_field": on_header_field,
        "on_header_value": on_header_value,
        "on_header_end": on_header_end,
        "on_headers_finished": on_headers_finished,
    }

    # Create parser and feed it data
    parser = MultipartParser(
        boundary, cast(Any, callbacks)
    )  # Note the cast is the easiest way to bypass a complex typing mechanic. You can't just import the underlying type as it is created inside an if TYPE_CHECKING block
    parser.write(request_body)
    parser.finalize()

    # Check if document part was found and handle size errors
    if document_part_too_large:
        raise MultipartParsingError(
            f"Document file is too large. Maximum allowed size is {MAX_FILE_SIZE // (1024 * 1024)}MB."
        )

    if not any(
        f in ("document.html", "document.txt", "document.pdf") for f in uploaded_files
    ):
        raise MultipartParsingError("Missing required 'document' part")

    logger.debug(
        "Multipart parts streamed to S3",
        extra={
            "s3_path": document_folder,
            "file_count": len(uploaded_files),
            "file_names": list(uploaded_files.keys()),
        },
    )

    return uploaded_files


@cache
def get_bearer_token() -> str:
    """Get bearer token from SSM Parameter Store with caching"""
    param_name = os.getenv("BEARER_TOKEN_PARAM_NAME")
    logger.debug(
        "Will fetch token from SSM Parameter Store", extra={"param_name": param_name}
    )

    if not param_name:
        logger.error("BEARER_TOKEN_PARAM_NAME environment variable not set")
        raise ValueError("BEARER_TOKEN_PARAM_NAME environment variable not set")

    try:
        response = ssm_client.get_parameter(Name=param_name, WithDecryption=True)
        logger.debug("Bearer token retrieved from SSM Parameter Store")
        return response["Parameter"]["Value"]
    except Exception as e:
        logger.error(
            "Failed to retrieve bearer token",
            extra={"error": str(e), "param_name": param_name},
        )
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


def _get_s3_folder_contents(bucket: str, folder: str):
    # Note that this will fetch only the first 1000 items. That's more than enough for our purposes
    response = s3_client.list_objects_v2(Bucket=bucket, Prefix=f"{folder}/")
    for c in response.get("Contents", []):
        yield c["Key"]


@contextmanager
def backup_in_case_of_error(bucket: str, document_folder: str):
    """Context manager to handle backup/restore logic for S3 folder operations"""
    backup_folder = f"{document_folder}.bak"
    folder_exists = False
    backup_created = False

    try:
        content_keys = list(_get_s3_folder_contents(bucket, document_folder))
        folder_exists = any(content_keys)

        if folder_exists:
            logger.debug(
                "Document folder exists, creating backup",
                extra={"folder": document_folder},
            )

            # Create backup by copying all objects
            for old_key in content_keys:
                s3_client.copy_object(
                    Bucket=bucket,
                    CopySource={"Bucket": bucket, "Key": old_key},
                    Key=old_key.replace(f"{document_folder}/", f"{backup_folder}/", 1),
                )

            backup_created = True
            logger.debug(
                "Backup created successfully", extra={"backup_folder": backup_folder}
            )

        # Delete existing folder contents
        if folder_exists:
            logger.debug(
                "Deleting existing document folder", extra={"folder": document_folder}
            )
            s3_client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": key} for key in content_keys]},
            )

        # Yield control to the calling code
        yield

    except Exception as e:
        logger.error("Error during folder operations", extra={"error": str(e)})

        # Restore from backup if it was created
        if backup_created:
            logger.debug("Restoring from backup due to error")
            try:
                current_content_keys = list(
                    _get_s3_folder_contents(bucket, document_folder)
                )
                if any(current_content_keys):
                    s3_client.delete_objects(
                        Bucket=bucket,
                        Delete={
                            "Objects": [{"Key": key} for key in current_content_keys]
                        },
                    )

                for old_key in _get_s3_folder_contents(bucket, backup_folder):
                    new_key = old_key.replace(
                        f"{backup_folder}/", f"{document_folder}/", 1
                    )

                    s3_client.copy_object(
                        Bucket=bucket,
                        CopySource={"Bucket": bucket, "Key": old_key},
                        Key=new_key,
                    )

                logger.debug("Backup restored successfully")
            except Exception as restore_error:
                logger.error(
                    "Failed to restore backup", extra={"error": str(restore_error)}
                )

        raise  # Re-raise the original exception

    finally:
        if backup_created:
            try:
                logger.debug(
                    "Cleaning up backup folder", extra={"backup_folder": backup_folder}
                )
                s3_client.delete_objects(
                    Bucket=bucket,
                    Delete={
                        "Objects": [
                            {"Key": key}
                            for key in _get_s3_folder_contents(bucket, backup_folder)
                        ]
                    },
                )
                logger.debug("Backup cleanup completed")
            except Exception as cleanup_error:
                logger.warning(
                    "Failed to clean up backup", extra={"error": str(cleanup_error)}
                )


def _unauthorized_request() -> Response:
    metrics.add_metric(name="UnauthorizedRequests", unit=MetricUnit.Count, value=1)
    return Response(
        status_code=401,
        content_type=content_types.APPLICATION_JSON,
        body={"error": "Unauthorized"},
    )


def authentication_middleware(
    app: APIGatewayHttpResolver, next_middleware: NextMiddleware
) -> Response:
    """Middleware to authenticate requests using bearer token"""

    headers = getattr(app.current_event, "headers", None) or {}
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
        logger.debug(
            "Lambda handler invoked",
            extra={
                "event_type": event.get("httpMethod", "unknown"),
                "path": event.get("path", "unknown"),
                "resource": event.get("resource", "unknown"),
                "request_context": event.get("requestContext", {}),
            },
        )

        result = app.resolve(event, context)
        logger.debug(
            "Request resolved", extra={"status_code": result.get("statusCode")}
        )
        return result
    except Exception as e:
        logger.exception(
            "Unhandled exception in lambda_handler",
            extra={
                "error": str(e),
                "error_type": type(e).__name__,
            },
        )
        metrics.add_metric(name="UnhandledExceptions", unit=MetricUnit.Count, value=1)

        # Return a proper API Gateway response
        return {
            "statusCode": 500,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(
                {
                    "error": "Internal server error",
                    "message": "An unexpected error occurred",
                }
            ),
        }
