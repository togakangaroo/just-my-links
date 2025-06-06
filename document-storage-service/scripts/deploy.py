#!/usr/bin/env python3
"""
Deploy script for the indexing service Lambda function.
Builds Docker image, pushes to ECR, and updates Lambda function if it exists.
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


def get_default_region():
    """Get the default region from the current AWS profile."""
    try:
        session = boto3.Session()
        return session.region_name or 'us-east-1'
    except Exception:
        return 'us-east-1'


def run_command(cmd, check=True, capture_output=False):
    """Run a shell command with proper error handling."""
    print(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            check=check,
            capture_output=capture_output,
            text=True
        )
        if capture_output:
            return result.stdout.strip()
        return result
    except subprocess.CalledProcessError as e:
        print(f"Command failed with exit code {e.returncode}")
        if capture_output and e.stdout:
            print(f"stdout: {e.stdout}")
        if capture_output and e.stderr:
            print(f"stderr: {e.stderr}")
        raise


def authenticate_docker_to_ecr(ecr_repository_uri, aws_region):
    """Authenticate Docker to ECR."""
    print("Checking Docker ECR authentication...")

    # Check if already logged in by trying to get the ECR authorization token
    ecr_client = boto3.client('ecr', region_name=aws_region)
    try:
        response = ecr_client.get_authorization_token()
        token = response['authorizationData'][0]['authorizationToken']
        endpoint = response['authorizationData'][0]['proxyEndpoint']

        # Decode the token (it's base64 encoded username:password)
        import base64
        username, password = base64.b64decode(token).decode().split(':')

        # Attempt to login to Docker
        process = subprocess.Popen(
            ['docker', 'login', '--username', username, '--password-stdin', endpoint],
            stdin=subprocess.PIPE,
            text=True
        )
        process.communicate(input=password)

        if process.returncode != 0:
            raise RuntimeError("Docker login failed")

        print("Successfully authenticated to ECR")

    except ClientError as e:
        print(f"Failed to get ECR authorization token: {e}")
        raise


def build_and_push_image(ecr_repository_uri, image_tag):
    """Build Docker image and push to ECR."""
    print("Building Docker image...")

    # Build the image
    run_command(['docker', 'build', '-t', 'indexing-service', '.'])

    # Tag for ECR
    print("Tagging image for ECR...")
    full_image_uri = f"{ecr_repository_uri}:{image_tag}"
    run_command(['docker', 'tag', 'indexing-service:latest', full_image_uri])

    # Push to ECR
    print("Pushing image to ECR...")
    run_command(['docker', 'push', full_image_uri])

    print(f"Image pushed to ECR: {full_image_uri}")
    return full_image_uri


def update_lambda_function(lambda_function_name, image_uri, aws_region):
    """Update Lambda function if it exists."""
    lambda_client = boto3.client('lambda', region_name=aws_region)

    print("Checking if Lambda function exists...")
    try:
        lambda_client.get_function(FunctionName=lambda_function_name)
        print(f"Lambda function {lambda_function_name} exists. Updating function code...")

        # Update function code
        response = lambda_client.update_function_code(
            FunctionName=lambda_function_name,
            ImageUri=image_uri
        )

        print("Waiting for function update to complete...")
        waiter = lambda_client.get_waiter('function_updated')
        waiter.wait(FunctionName=lambda_function_name)

        print("Lambda function updated successfully!")
        return True

    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceNotFoundException':
            print(f"Lambda function {lambda_function_name} does not exist. Skipping function update.")
            print("Deploy the CloudFormation stack to create the Lambda function.")
            return False
        else:
            print(f"Error checking Lambda function: {e}")
            raise


def main():
    parser = argparse.ArgumentParser(description="Deploy indexing service to AWS")
    parser.add_argument(
        'environment',
        nargs='?',
        default='dev',
        help='Environment name (default: dev)'
    )
    parser.add_argument(
        '--region',
        default=get_default_region(),
        help=f'AWS region (default: {get_default_region()})'
    )
    parser.add_argument(
        '--image-tag',
        default='latest',
        help='Docker image tag (default: latest)'
    )

    args = parser.parse_args()

    # Configuration
    environment = args.environment
    aws_region = args.region
    image_tag = args.image_tag

    # Construct resource names
    ecr_repository_uri = f"145216492484.dkr.ecr.{aws_region}.amazonaws.com/{environment}-just-my-links-lambda"
    lambda_function_name = f"{environment}-just-my-links-index-document"

    print(f"Building and deploying Lambda container for environment: {environment}")

    try:
        # Change to the indexing-service directory
        script_dir = Path(__file__).parent
        service_dir = script_dir.parent
        original_cwd = Path.cwd()

        print(f"Changing to directory: {service_dir}")
        import os
        os.chdir(service_dir)

        # Authenticate Docker to ECR
        authenticate_docker_to_ecr(ecr_repository_uri, aws_region)

        # Build and push image
        image_uri = build_and_push_image(ecr_repository_uri, image_tag)

        # Update Lambda function if it exists
        update_lambda_function(lambda_function_name, image_uri, aws_region)

        print("Deployment complete!")

    except Exception as e:
        print(f"Deployment failed: {e}")
        sys.exit(1)
    finally:
        # Change back to original directory
        os.chdir(original_cwd)


if __name__ == "__main__":
    main()
