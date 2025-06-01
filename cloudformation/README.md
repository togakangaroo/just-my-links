# Just My Links - CloudFormation Infrastructure

This directory contains the CloudFormation templates for the Just My Links application infrastructure.

## Architecture Overview

The infrastructure consists of:
- **VPC & Networking**: Private subnets, security groups for Lambda
- **Storage**: EFS for ChromaDB, S3 for log backup, ECR for container images
- **Compute**: Lambda function with EFS mount, API Gateway REST endpoint
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

- `main.yaml` - Root stack that orchestrates nested stacks
- `templates/networking.yaml` - VPC, subnets, security groups
- `templates/storage.yaml` - EFS, S3, ECR repositories
- `templates/compute.yaml` - Lambda function, API Gateway
- `templates/monitoring.yaml` - CloudWatch alarms, SNS topics
- `templates/secrets.yaml` - Secrets Manager resources
- `parameters/` - Environment-specific parameter files
