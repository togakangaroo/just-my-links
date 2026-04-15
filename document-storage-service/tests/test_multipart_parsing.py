"""Tests for multipart form data parsing in _stream_multipart_to_s3."""

import base64
from unittest.mock import MagicMock, patch
import pytest


def make_multipart_body(
    boundary: str,
    content: str,
    content_type: str = "text/html",
    filename: str = "document.html",
) -> bytes:
    return (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="document"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n"
        f"\r\n"
        f"{content}\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")


def make_event(boundary: str, body: bytes, base64_encode: bool = True):
    """Build a minimal API Gateway v2 event dict."""
    if base64_encode:
        encoded_body = base64.b64encode(body).decode("utf-8")
        is_base64 = True
    else:
        encoded_body = body.decode("utf-8")
        is_base64 = False

    return {
        "version": "2.0",
        "headers": {"content-type": f"multipart/form-data; boundary={boundary}"},
        "isBase64Encoded": is_base64,
        "body": encoded_body,
        "queryStringParameters": {"url": "https://example.com/test"},
        "requestContext": {
            "http": {
                "method": "PUT",
                "path": "/document",
                "protocol": "HTTP/1.1",
                "sourceIp": "1.2.3.4",
                "userAgent": "test",
            },
            "requestId": "test-123",
            "stage": "$default",
        },
        "routeKey": "PUT /document",
        "rawPath": "/document",
        "rawQueryString": "url=https://example.com/test",
    }


@pytest.fixture
def mock_aws(monkeypatch):
    """Patch AWS clients so tests don't hit real AWS."""
    mock_s3 = MagicMock()
    mock_s3.put_object.return_value = {}
    mock_s3.list_objects_v2.return_value = {"KeyCount": 0}

    mock_secrets = MagicMock()
    mock_eventbridge = MagicMock()

    monkeypatch.setenv("APPLICATION_BUCKET", "test-bucket")
    monkeypatch.setenv("EVENT_BUS_NAME", "test-bus")
    monkeypatch.setenv("BEARER_TOKEN_PARAM_NAME", "/just-my-links/auth-token/test")

    with (
        patch("boto3.client") as mock_boto3_client,
    ):

        def client_factory(service, **kwargs):
            if service == "s3":
                return mock_s3
            elif service == "ssm":
                return mock_secrets
            elif service == "events":
                return mock_eventbridge
            return MagicMock()

        mock_boto3_client.side_effect = client_factory
        yield mock_s3


def test_html_part_appears_in_uploaded_files(mock_aws):
    """Regression: uploaded_files must use the filename key (document.html),
    not the form-field name (document)."""
    from aws_lambda_powertools.utilities.data_classes import APIGatewayProxyEventV2

    import sys

    # Clear cached module so env vars & patches apply
    sys.modules.pop("app", None)
    import app as app_module

    # Reset cached get_documents_folder (uses @cache)
    app_module.get_documents_folder.cache_clear()

    boundary = "testboundary1234"
    html_content = "<html><body><h1>Test</h1></body></html>"
    body = make_multipart_body(boundary, html_content, "text/html")
    raw_event = make_event(boundary, body, base64_encode=True)
    event = APIGatewayProxyEventV2(raw_event)

    result = app_module._stream_multipart_to_s3(event, "abc123sha256hash")

    assert (
        "document.html" in result
    ), f"Expected 'document.html' in uploaded_files but got keys: {list(result.keys())}"
    assert result["document.html"] == len(html_content.encode("utf-8"))


def test_plain_text_part_appears_in_uploaded_files(mock_aws):
    """Plain text content should produce a document.txt key."""
    from aws_lambda_powertools.utilities.data_classes import APIGatewayProxyEventV2
    import sys

    sys.modules.pop("app", None)
    import app as app_module

    app_module.get_documents_folder.cache_clear()

    boundary = "textboundary5678"
    text_content = "Just plain text content here."
    body = make_multipart_body(
        boundary, text_content, "text/plain", filename="document.txt"
    )
    raw_event = make_event(boundary, body, base64_encode=True)
    event = APIGatewayProxyEventV2(raw_event)

    result = app_module._stream_multipart_to_s3(event, "def456sha256hash")

    assert (
        "document.txt" in result
    ), f"Expected 'document.txt' in uploaded_files but got keys: {list(result.keys())}"


def test_missing_document_part_raises_error(mock_aws):
    """A multipart body without a 'document' field should raise MultipartParsingError."""
    from aws_lambda_powertools.utilities.data_classes import APIGatewayProxyEventV2
    import sys

    sys.modules.pop("app", None)
    import app as app_module

    app_module.get_documents_folder.cache_clear()

    boundary = "noboundary9999"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="other_field"\r\n'
        "\r\n"
        "some value\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    raw_event = make_event(boundary, body, base64_encode=False)
    event = APIGatewayProxyEventV2(raw_event)

    with pytest.raises(
        app_module.MultipartParsingError, match="Missing required 'document' part"
    ):
        app_module._stream_multipart_to_s3(event, "ghi789sha256hash")
