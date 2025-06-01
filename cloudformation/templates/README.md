# CloudFormation Templates

## Architecture Decision: Single Template vs Nested Stacks

This project uses a single monolithic CloudFormation template (`main.yaml`) instead of nested stacks for the following reasons:

### Why Not Nested Stacks?

**Template URL Requirement**: CloudFormation nested stacks require child templates to be accessible via HTTPS URLs (typically S3). This adds deployment complexity:
- Templates must be uploaded to S3 before deployment
- S3 bucket management and permissions
- Template versioning and lifecycle management
- Additional failure points in the deployment process

**Small Template Size**: Our infrastructure templates are relatively small and don't benefit from the separation that nested stacks provide for large, complex deployments.

**Deployment Simplicity**: A single template means:
- One-step deployment with `aws cloudformation deploy`
- No dependency on external template storage
- Easier CI/CD pipeline integration
- Simpler parameter management

### Template Organization

The single `main.yaml` template is organized into logical sections with clear comment delimiters:

1. **Networking Resources** - VPC, subnets, security groups
2. **Secrets Resources** - AWS Secrets Manager for sensitive data
3. **Storage Resources** - EFS, ECR, S3 buckets
4. **Monitoring Resources** - SNS, CloudWatch, EventBridge
5. **Compute Resources** - Lambda, API Gateway, IAM roles

### When to Consider Nested Stacks

Nested stacks would be beneficial if:
- Templates exceed CloudFormation size limits (51,200 bytes)
- You need to reuse templates across multiple projects
- You have complex cross-stack dependencies
- You're managing infrastructure at enterprise scale

For this project's scope, a single template provides the best balance of simplicity and maintainability.
