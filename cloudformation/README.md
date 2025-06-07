# Just My Links - CloudFormation Infrastructure

This directory contains the CloudFormation templates for the Just My Links application infrastructure.

## Architecture Overview

The infrastructure consists of:
- **Storage**: S3 for ChromaDB and log backup, ECR for container images
- **Compute**: Lambda functions, API Gateway HTTP endpoints
- **Events**: EventBridge custom bus for document storage events
- **Monitoring**: CloudWatch alarms, SNS notifications for S3 size alerts
- **Security**: Secrets Manager for bearer token authentication

## Deployment

1. Validate templates:
   ```bash
   ./scripts/validate.sh
   ```

2. Deploy to development:
   ```bash
   ./scripts/deploy.sh dev
   ```

   Note that this is where default tags will be set

## Template Structure

- `main.yaml` - Complete infrastructure stack. Note that we are using just a single stack file as using multiple will require copying to s3 before running the cloudformation deploy command
- `parameters/` - Environment-specific parameter files
