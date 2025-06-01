#!/bin/bash

set -e

ENVIRONMENT=${1:-dev}
shift  # Remove the first argument (environment) from $@
STACK_NAME="just-my-links-${ENVIRONMENT}"

echo "Deploying Just My Links infrastructure for environment: $ENVIRONMENT"

# Check if parameters file exists
if [ ! -f "parameters/${ENVIRONMENT}.env" ]; then
    echo "Error: parameters/${ENVIRONMENT}.env not found"
    exit 1
fi

# Read parameters from file and pass to parameter-overrides
PARAMS=$(cat parameters/${ENVIRONMENT}.env | tr '\n' ' ')

# Add any additional parameters passed as arguments
ADDITIONAL_PARAMS="$@"

# Deploy the stack
aws cloudformation deploy \
    --template-file templates/main.yaml \
    --stack-name $STACK_NAME \
    --parameter-overrides $PARAMS $ADDITIONAL_PARAMS \
    --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
    --no-fail-on-empty-changeset

echo "Deployment complete!"

# Output the API Gateway URL
API_URL=$(aws cloudformation describe-stacks \
    --stack-name $STACK_NAME \
    --query 'Stacks[0].Outputs[?OutputKey==`ApiGatewayUrl`].OutputValue' \
    --output text)

echo "API Gateway URL: $API_URL"
