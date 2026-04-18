# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "boto3>=1.35.0",
#   "requests>=2.31.0",
# ]
# ///
"""
Just My Links CLI — save and search documents via the API.

Configuration (API URL and bearer token) is fetched automatically from CloudFormation and SSM.

Usage:
    ./scripts/jml.py save <url> [--title <title>] [--file <path>] [--env dev]
    ./scripts/jml.py search <query> [--top 8] [--env dev]

Examples:
    ./scripts/jml.py save https://example.com/article --title "My Article" --file page.html
    cat page.html | ./scripts/jml.py save https://example.com/article
    ./scripts/jml.py search "machine learning"
    ./scripts/jml.py search "python #tutorial" --top 10
"""

import argparse
import mimetypes
import os
import sys
from pathlib import Path

import boto3
import requests

DEFAULT_TOP_K = 8
DEFAULT_ENV = "dev"
DEFAULT_REGION = "us-east-1"

_CONTENT_TYPE_TO_FILENAME = {
    "text/html": "document.html",
    "text/plain": "document.txt",
    "application/pdf": "document.pdf",
}


def _get_session(region: str) -> boto3.Session:
    profile = os.environ.get("AWS_PROFILE", "just-my-links")
    return boto3.Session(profile_name=profile, region_name=region)


def _get_api_config(env: str, session: boto3.Session) -> tuple[str, str]:
    """Return (api_url, bearer_token) from CloudFormation outputs and SSM."""
    cf = session.client("cloudformation")
    stack_name = f"just-my-links-{env}"
    print(
        f"Fetching config from CloudFormation stack {stack_name!r}...", file=sys.stderr
    )

    response = cf.describe_stacks(StackName=stack_name)
    outputs = {
        o["OutputKey"]: o["OutputValue"] for o in response["Stacks"][0]["Outputs"]
    }

    api_url = outputs.get("DocumentStorageHttpApiUrl")
    if not api_url:
        raise SystemExit(
            f"Stack {stack_name!r} has no DocumentStorageHttpApiUrl output.\n"
            "Is this environment fully deployed (IsNotFirstRun=true)?"
        )

    token_param_name = outputs["AuthTokenParameterName"]
    ssm = session.client("ssm")
    token = ssm.get_parameter(Name=token_param_name)["Parameter"]["Value"]

    return api_url, token


def _detect_content_type(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    if mime in _CONTENT_TYPE_TO_FILENAME:
        return mime
    return "text/plain"


def cmd_save(args: argparse.Namespace, api_url: str, token: str) -> int:
    if args.file:
        file_path = Path(args.file)
        content = file_path.read_bytes()
        content_type = _detect_content_type(file_path)
    elif not sys.stdin.isatty():
        content = sys.stdin.buffer.read()
        content_type = "text/plain"
    else:
        print("Error: provide document content via --file or stdin.", file=sys.stderr)
        return 1

    filename = _CONTENT_TYPE_TO_FILENAME[content_type]
    params: dict[str, str] = {"url": args.url}
    if args.title:
        params["title"] = args.title

    print(f"Saving {args.url!r}...", file=sys.stderr)
    response = requests.put(
        f"{api_url}/document",
        params=params,
        files={"document": (filename, content, content_type)},
        headers={"Authorization": f"Bearer {token}"},
    )

    if response.status_code == 200:
        data = response.json()
        print(data.get("message", "Saved."))
        if files := data.get("files"):
            print(f"Files: {', '.join(files)}")
        return 0

    print(f"Error {response.status_code}: {response.text}", file=sys.stderr)
    return 1


def cmd_search(args: argparse.Namespace, api_url: str, token: str) -> int:
    response = requests.get(
        f"{api_url}/search",
        params={"q": args.query, "top": args.top},
        headers={"Authorization": f"Bearer {token}"},
    )

    if response.status_code != 200:
        print(f"Error {response.status_code}: {response.text}", file=sys.stderr)
        return 1

    data = response.json()
    sections = data.get("sections", {})

    if not sections:
        print("No results found.")
        return 0

    section_labels = {
        "vector": "Semantic matches",
        "title": "Title matches",
        "tags": "Tag matches",
    }

    print(f"\nResults for: {args.query!r}\n")
    for key in ("vector", "title", "tags"):
        results = sections.get(key)
        if not results:
            continue
        print(f"  {section_labels[key]}:")
        for i, item in enumerate(results, 1):
            url = item["url"]
            title = item.get("title")
            if title and title != url:
                print(f"    {i}. {title}")
                print(f"       {url}")
            else:
                print(f"    {i}. {url}")
            if tags := item.get("matched_tags"):
                print(f"       tags: {', '.join(f'#{t}' for t in tags)}")
        print()

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Just My Links CLI — save and search documents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--env", default=DEFAULT_ENV, help=f"Environment (default: {DEFAULT_ENV})"
    )
    parser.add_argument(
        "--region",
        default=DEFAULT_REGION,
        help=f"AWS region (default: {DEFAULT_REGION})",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    save_parser = subparsers.add_parser("save", help="Save a document")
    save_parser.add_argument("url", help="URL of the document")
    save_parser.add_argument("--title", help="Document title")
    save_parser.add_argument(
        "--file", metavar="PATH", help="Path to document file (HTML, text, or PDF)"
    )

    search_parser = subparsers.add_parser("search", help="Search saved documents")
    search_parser.add_argument(
        "query", help="Search query (use #tag for tag filtering)"
    )
    search_parser.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP_K,
        help=f"Number of results (default: {DEFAULT_TOP_K})",
    )

    args = parser.parse_args()

    session = _get_session(args.region)
    api_url, token = _get_api_config(args.env, session)

    if args.command == "save":
        sys.exit(cmd_save(args, api_url, token))
    elif args.command == "search":
        sys.exit(cmd_search(args, api_url, token))


if __name__ == "__main__":
    main()
