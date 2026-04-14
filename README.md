<!-- This file is auto-generated from README.ipynb. DO NOT MODIFY IT DIRECTLY. -->
A self-hosted [Pocket](https://getpocket.com/) replacement built on AWS. Save pages from a Chrome extension, search them later with semantic search.

# Goals

- Chrome extension to save any page (HTML or PDF)
- Semantic search across saved content
- Low cost — stays within AWS free tier or close to it
- Single-tenant (your own instance, not shared)

## Roadmap

- Android share-to app
- CI/CD
- Paywall bypass / archive.org fallback

# Setup


## Prerequisites


- [uv](https://docs.astral.sh/uv/getting-started/installation/) — Python dependency manager and script runner
- [pre-commit](https://pre-commit.com/#install) — installs git hooks for linting, formatting, and README sync

After cloning, install the git hooks:


```python
!pre-commit install
```

## Running the Notebook


Start the Jupyter server from this folder:

```bash
uv run jupyter lab
```

## AWS Account Configuration


Optional but recommended: follow the steps in the jupyter notebook here to set it up [here.](./aws-configuration.ipynb)

Recommend setting up an AWS SSO profile named `just-my-links` as that's what this notebook uses 

Afterwards, you'll need to log in with the following occasionally


```python
!aws sso login --profile just-my-links
```

### Deploying


#### First-time setup

Deploy infrastructure (ECR, S3, SQS, EventBridge) without Lambda functions (images don't exist yet)


```python
!cd cloudformation && IsFirstRun=true AWS_PROFILE=just-my-links ./scripts/deploy.sh
```

#### Subsequent deploys

Bilds Docker images, pushes to ECR, updates CloudFormation and Lambda


```python
!cd cloudformation && AWS_PROFILE=just-my-links ./scripts/deploy.sh dev
```

### Getting Your API Credentials

Run the cells below to fetch the API URL and bearer token for the `dev` environment.


```python
!AWS_PROFILE=just-my-links aws cloudformation describe-stacks \
    --stack-name just-my-links-dev \
    --query "Stacks[0].Outputs[?OutputKey=='DocumentStorageHttpApiUrl'].OutputValue" \
    --output text
```


```python
!AWS_PROFILE=just-my-links aws secretsmanager get-secret-value \
    --secret-id just-my-links--auth-token--dev \
    --query SecretString \
    --output text
```

### Chrome Extension

1. Open `chrome://extensions` in Chrome
2. Enable **Developer mode** (top right toggle)
3. Click **Load unpacked** and select the `chrome-extension/` directory in this repo
4. Click the extension icon → **Settings** and paste in the API URL and bearer token from the cells above

# Architecture


This application makes heavy use of mostly-free-tier AWS serverless infrastructure spread across several components and clients.

## Naming and Conventions

We want to support multiple environments (eg `dev` and `prod`) and will use suffix resource names. All resources should also be tagged `just-my-links: environment-name`. Below when we say `dev` we really mean whichever environment is selected.

Note that we do not use an AWS vpc as we're trying to keep costs to virtually zero, we would need a  vpc endpoint for our lambda to communicate with secret manager and vpc endpoints cost $8/mo.

All operations in s3 will be within a single bucket eg `just-my-links--dev`

We don't want to pay for a server and this is low use so will be implemented via an api gateway http endpoint that triggers AWS lambda. The lambda will be named `just-my-links--store-document--dev` and deployed via a container that is stored in an ecr registry (`just-my-links--ecr-store-document--dev`). Other lambdas will follow a similar pattern

This service implementation will use aws lambda powertools for their good logging defaults, typing, api handling structures, and so on

Logs need to be written to s3 for backup. There should be alerts on that bucket filling up to be too large that email me.

# Google Chrome Extension

A chrome browser extension that can take the full content of the current web page as html or text, the url, and sends it (and maybe some images) to our document store service.

This maintains our api key in a the browser secret store and also has a simple search interface which hits our search lambda.

## Document Store interface

The backend our documents are submitted to implemented via an AWS Lambda.

Within that service we are going to be receiving a multipart formdata request and a document_url that is part of our querystring. we will write it to a document-storage folder in s3 under a key that is the hash of the document_url. All files will be stored under their multipart form data filename. The first one must be named document.html or document.txt. We will then write a .metadata.json file that will contain the documentUrl and the "entrypoint" (which will be `document.txt` or `document.html`).

Finally, the lambda will broadcast an event via eventbridge (named `just-my-links--events--dev`) with a `type: "Document stored"`. Which contains the folderPath in s3 and the documentUrl.

Only one of these lambdas should run at a time. The API Gateway should have authentication though all it's going to do is safely compare a submitted bearer token against an auth token stored in secret manager

## Index Documents Service

An eventbridge rule will configure events of the above type to pass into an sqs queue `just-my-links--index-documents-trigger--dev`. This will trigger a separate lambda named `just-my-links--index-documents` (again, concurrency 1 - this is important as we're going to be copying/writing a single db file here). Do not use async invocation here, just wire up the sqs queue to trigger the lambda. Also create a dead letter queue (`just-my-links--index-documents-dlq--dev`) and have a montior that sends emails when something enters that queue.

Our `just-my-links--index-documents`  will copy our embeddings file from s3 (if it exists) into the lambda's /tmp directory and open it. For each received event it will read the event's folder and read in its .metadata.json. It will then upsert an array under the key of the documentUrl. The array will contain the contents of entrypoint as the first value and any other txt or html file contents as the remainder. Broadcast an event to our eventbridge bus `type: "Document indexed"` with the `folderPath` and `documentUrl`. Then the db file should be copied back to s3.

## CLI


We will create a command line tool - it can be a python script. This will demostrate search locally by copying our embeddings db to a local tmp directory and allow querying of it.

## Android Application (TODO)

To support bookmarking on mobile we should have a simple application that websites can be "shared" to. It should use the device browser to open up the site so that it can grab text in the user's logged in context.

We don't want to deal with the app store so sideloading it works

# Demos

Set your API URL and token once, then run the cells below.

> **Note**: The token is sensitive — don't commit cell outputs. Use `Cell → All Output → Clear` before saving.


```python
import subprocess

# Fetch the API URL from CloudFormation and token from Secrets Manager
API_URL = subprocess.check_output(
    "AWS_PROFILE=just-my-links aws cloudformation describe-stacks "
    "--stack-name just-my-links-dev "
    "--query \"Stacks[0].Outputs[?OutputKey=='DocumentStorageHttpApiUrl'].OutputValue\" "
    "--output text",
    shell=True,
    text=True,
).strip()

TOKEN = subprocess.check_output(
    "AWS_PROFILE=just-my-links aws secretsmanager get-secret-value "
    "--secret-id just-my-links--auth-token--dev "
    "--query SecretString --output text",
    shell=True,
    text=True,
).strip()

print(f"API_URL: {API_URL}")
```

## Store a Document

Send an HTML page to the API. The pipeline will store it in S3, then asynchronously chunk + embed it via Bedrock and add it to the search index (takes ~30–60s depending on article length).


```python
import json
import urllib.parse
import urllib.request

# Fetch and store an article from the web
doc_url = "https://blog.skypilot.co/research-driven-agents/"
html = urllib.request.urlopen(doc_url).read().decode("utf-8")

req = urllib.request.Request(
    f"{API_URL}/document?url={urllib.parse.quote(doc_url)}",
    method="PUT",
)
req.add_header("Authorization", f"Bearer {TOKEN}")

boundary = "----FormBoundary7MA4YWxkTrZu0gW"
req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
body = (
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="document"; filename="document.html"\r\n'
    f"Content-Type: text/html\r\n\r\n" + html + f"\r\n--{boundary}--\r\n"
).encode("utf-8")
req.data = body

resp = urllib.request.urlopen(req)
print(json.loads(resp.read()))
```

## Semantic Search — API

Once a document is indexed, search it via the HTTP endpoint. Returns ranked URLs with distance scores (lower distance = closer match).


```python
import urllib.request
import urllib.parse
import json

query = "how do agents reason about tasks"
url = f"{API_URL}/search?q={urllib.parse.quote(query)}&top=5"
req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
resp = json.loads(urllib.request.urlopen(req).read())

print(f"Query: {resp['query']}\n")
for i, r in enumerate(resp["results"], 1):
    print(f"  {i}. {r['url']}")
    print(f"     distance: {r['distance']:.4f}")
```

The Chrome extension also has a built-in search tab — click the extension icon and switch to **Search**.

## Semantic Search — CLI

The CLI tool is faster for local experimentation — it caches the index at `~/.cache/just-my-links/` so subsequent queries don't re-download from S3.



```python
!uv run scripts/search.py "how do agents reason about tasks"
```


```python
!uv run scripts/search.py "LLM planning benchmarks" --top 10 --no-cache
```


```python
!uv run scripts/search.py "my query" --env prod
```
