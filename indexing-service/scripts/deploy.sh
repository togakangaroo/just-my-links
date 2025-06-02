#!/bin/bash

set -e

# Configuration
ECR_REPOSITORY_URI="145216492484.dkr.ecr.us-east-1.amazonaws.com/dev-just-my-links-lambda"
AWS_REGION="us-east-1"
IMAGE_TAG="latest"

echo "Building and deploying Lambda container to ECR..."

# Authenticate Docker to ECR
echo "Authenticating Docker with ECR..."
aws ecr get-login-password --region $AWS_REGION | docker login --username AWS --password-stdin $ECR_REPOSITORY_URI

# Build the Docker image
echo "Building Docker image..."
docker build -t indexing-service .

# Tag the image for ECR
echo "Tagging image for ECR..."
docker tag indexing-service:latest $ECR_REPOSITORY_URI:$IMAGE_TAG

# Push the image to ECR
echo "Pushing image to ECR..."
docker push $ECR_REPOSITORY_URI:$IMAGE_TAG

echo "Deployment complete!"
echo "Image URI: $ECR_REPOSITORY_URI:$IMAGE_TAG"
