#!/bin/bash

set -e

ENVIRONMENT=${1:-dev}
STACK_NAME="just-my-links-${ENVIRONMENT}"

# Use git SHA as the image tag so CloudFormation detects every new push
IMAGE_TAG=$(git rev-parse --short HEAD)

echo "Deploying Just My Links for environment: $ENVIRONMENT (image tag: $IMAGE_TAG)"

# Check if parameters file exists
if [ ! -f "parameters/${ENVIRONMENT}.env" ]; then
    echo "Error: parameters/${ENVIRONMENT}.env not found"
    exit 1
fi

# --- ECR / Docker setup ---
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
AWS_REGION=$(aws configure get region || echo "us-east-1")

STORE_DOCUMENT_REPO="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/just-my-links-ecr-store-document-${ENVIRONMENT}"
INDEX_DOCUMENTS_REPO="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/just-my-links-ecr-index-documents-${ENVIRONMENT}"

echo "Authenticating Docker to ECR..."
aws ecr get-login-password --region "$AWS_REGION" \
    | docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "Building document-storage-service image..."
docker build -t "document-storage-service:${IMAGE_TAG}" "${REPO_ROOT}/document-storage-service"
docker tag "document-storage-service:${IMAGE_TAG}" "${STORE_DOCUMENT_REPO}:${IMAGE_TAG}"

echo "Building index-documents-service image..."
docker build -t "index-documents-service:${IMAGE_TAG}" "${REPO_ROOT}/index-documents-service"
docker tag "index-documents-service:${IMAGE_TAG}" "${INDEX_DOCUMENTS_REPO}:${IMAGE_TAG}"

echo "Pushing images to ECR..."
docker push "${STORE_DOCUMENT_REPO}:${IMAGE_TAG}"
docker push "${INDEX_DOCUMENTS_REPO}:${IMAGE_TAG}"

# --- CloudFormation deploy ---
PARAMS=$(cat "parameters/${ENVIRONMENT}.env" | tr '\n' ' ')

echo "Deploying CloudFormation stack..."
aws cloudformation deploy \
    --template-file templates/main.yaml \
    --stack-name "$STACK_NAME" \
    --parameter-overrides $PARAMS "ImageTag=${IMAGE_TAG}" \
    --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
    --tags "just-my-links=${ENVIRONMENT}" \
    --no-fail-on-empty-changeset

echo "Deployment complete!"

aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --query 'Stacks[0].Outputs'
