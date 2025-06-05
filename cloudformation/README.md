# Just My Links - CloudFormation Infrastructure

This directory contains the CloudFormation templates for the Just My Links application infrastructure.

## Architecture Overview

The infrastructure consists of:
- **Storage**: S3 for ChromaDB and log backup, ECR for container images
- **Compute**: Lambda function, API Gateway HTTP endpoint
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

## Template Structure

- `main.yaml` - Complete infrastructure stack
- `parameters/` - Environment-specific parameter files
