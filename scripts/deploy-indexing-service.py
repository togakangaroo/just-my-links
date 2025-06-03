#!/usr/bin/env -S uv run python
"""
Deploy script for the indexing service Lambda function.
Builds Docker image, pushes to ECR, and updates Lambda function if it exists.
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path
import os  # Added import for os

import boto3
from botocore.exceptions import ClientError


def get_default_region():
    """Get the default region from the current AWS profile."""
    try:
        session = boto3.Session()
        return session.region_name or 'us-east-1'
    except Exception:
        return 'us-east-1'


def run_command(cmd, check=True, verbose=False):
    """Run a shell command with proper error handling."""
    print(f"Running: {' '.join(cmd)}")
    try:
        if verbose:
            # In verbose mode, don't capture output so it prints in real-time
            result = subprocess.run(cmd, check=check, text=True)
            return result
        else:
            result = subprocess.run(
                cmd,
                check=check,
                capture_output=True,
                text=True
            )
            return result
    except subprocess.CalledProcessError as e:
        print(f"Command failed with exit code {e.returncode}")
        if e.stdout:
            print(f"stdout: {e.stdout}")
        if e.stderr:
            print(f"stderr: {e.stderr}")
        raise


def authenticate_docker_to_ecr(ecr_repository_uri, aws_region, aws_account_id, verbose=False):
    """Authenticate Podman/Docker to ECR."""
    print("Authenticating to ECR...")

    # Use aws ecr get-login-password which works better with Podman
    try:
        # Get the ECR password using AWS CLI
        get_password_cmd = ['aws', 'ecr', 'get-login-password', '--region', aws_region]
        if verbose:
            print(f"Running: {' '.join(get_password_cmd)}")
        
        password_result = subprocess.run(
            get_password_cmd,
            capture_output=True,
            text=True,
            check=True
        )
        password = password_result.stdout.strip()

        # Login using podman (which is aliased to docker)
        registry_url = f"{aws_account_id}.dkr.ecr.{aws_region}.amazonaws.com"
        login_cmd = ['docker', 'login', '--username', 'AWS', '--password-stdin', registry_url]
        
        if verbose:
            print(f"Running: {' '.join(login_cmd[:-1])} [password hidden]")
        
        process = subprocess.Popen(
            login_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE if not verbose else None,
            stderr=subprocess.PIPE if not verbose else None,
            text=True
        )
        stdout, stderr = process.communicate(input=password)

        if verbose and stdout:
            print(f"stdout: {stdout}")
        if verbose and stderr:
            print(f"stderr: {stderr}")

        if process.returncode != 0:
            raise RuntimeError("ECR login failed")

        print("Successfully authenticated to ECR")

    except subprocess.CalledProcessError as e:
        print(f"Failed to get ECR password: {e}")
        if e.stderr:
            print(f"stderr: {e.stderr}")
        raise
    except Exception as e:
        print(f"Failed to authenticate to ECR: {e}")
        raise


def build_and_push_image(ecr_repository_uri, image_tag, aws_region, aws_account_id, verbose=False):
    """Build Docker image and push to ECR."""
    print("Building Docker image...")

    # Build the image
    run_command(['docker', 'build', '-t', 'indexing-service', './indexing-service'], verbose=verbose)

    # Tag for ECR
    print("Tagging image for ECR...")
    full_image_uri = f"{ecr_repository_uri}:{image_tag}"
    run_command(['docker', 'tag', 'indexing-service:latest', full_image_uri], verbose=verbose)

    # Push to ECR with retry logic and re-authentication for Podman
    print("Pushing image to ECR...")
    max_retries = 5
    for attempt in range(max_retries):
        try:
            # Re-authenticate before each push attempt to avoid token expiration
            if attempt > 0:
                print(f"Re-authenticating before attempt {attempt + 1}...")
                authenticate_docker_to_ecr(ecr_repository_uri, aws_region, aws_account_id, verbose)
            
            # Use --force-compression to avoid blob checking issues with Podman
            run_command(['docker', 'push', '--force-compression', full_image_uri], verbose=verbose)
            print(f"Image pushed to ECR: {full_image_uri}")
            return full_image_uri
        except subprocess.CalledProcessError as e:
            if attempt < max_retries - 1:
                print(f"Push attempt {attempt + 1} failed, retrying in 10 seconds...")
                time.sleep(10)  # Wait longer between retries
            else:
                print(f"All push attempts failed")
                raise

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
    parser.add_argument(
        '--aws-account-id',
        default='145216492484',  # Default to current account ID
        help='AWS Account ID (default: 145216492484)'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Print verbose output including all command stdout/stderr'
    )

    args = parser.parse_args()

    # Configuration
    environment = args.environment
    aws_region = args.region
    image_tag = args.image_tag
    aws_account_id = args.aws_account_id
    verbose = args.verbose

    # Construct resource names
    ecr_repository_uri = f"{aws_account_id}.dkr.ecr.{aws_region}.amazonaws.com/{environment}-just-my-links-lambda"
    lambda_function_name = f"{environment}-just-my-links-index-document"

    print(f"Building and deploying Lambda container for environment: {environment}")

    try:
        # Store original directory
        original_cwd = Path.cwd()

        # Authenticate Docker to ECR
        authenticate_docker_to_ecr(ecr_repository_uri, aws_region, aws_account_id, verbose)

        # Build and push image
        image_uri = build_and_push_image(ecr_repository_uri, image_tag, aws_region, aws_account_id, verbose)

        # Update Lambda function if it exists
        update_lambda_function(lambda_function_name, image_uri, aws_region)

        print("Deployment complete!")

    except Exception as e:
        print(f"Deployment failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
