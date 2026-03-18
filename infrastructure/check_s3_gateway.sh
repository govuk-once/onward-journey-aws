#!/bin/bash

# ==============================================================================
# S3 Gateway Endpoint Discovery Script
# ==============================================================================
# PURPOSE:
#   Checks for existing S3 Gateway Endpoints in a specific VPC to prevent
#   'RouteAlreadyExists' errors during shared-VPC Terraform deployments.
#
# ARGUMENTS:
#   $1 - VPC_ID: The ID of the VPC to search in.
#   $2 - REGION: The AWS region (e.g., eu-west-2).
#   $3 - ENV_NAME: The environment prefix (e.g., sw, al) used for tag matching.
#
# OUTPUT (JSON):
#   - is_owner: "true" if the endpoint's Name tag matches the ENV_NAME.
#   - id: The VpcEndpointId of the discovered or owned gateway.
# ==============================================================================

VPC_ID=$1
REGION=$2
ENV_NAME=$3

# If ENV_NAME is somehow empty, default to 'unknown' to prevent false owner matches
ENV_NAME=${ENV_NAME:-unknown}

# Check for OUR specific endpoint
MY_ENDPOINT=$(aws ec2 describe-vpc-endpoints \
  --filters "Name=vpc-id,Values=$VPC_ID" "Name=tag:Name,Values=$ENV_NAME-s3-gateway" \
  --query "VpcEndpoints[0].VpcEndpointId" --output text --region $REGION)

# Check for ANY S3 Gateway
ANY_ENDPOINT=$(aws ec2 describe-vpc-endpoints \
  --filters "Name=vpc-id,Values=$VPC_ID" "Name=service-name,Values=com.amazonaws.$REGION.s3" "Name=vpc-endpoint-type,Values=Gateway" \
  --query "VpcEndpoints[0].VpcEndpointId" --output text --region $REGION)

if [ "$MY_ENDPOINT" != "None" ] && [ "$MY_ENDPOINT" != "" ]; then
  echo "{\"is_owner\": \"true\", \"id\": \"$MY_ENDPOINT\"}"
elif [ "$ANY_ENDPOINT" != "None" ] && [ "$ANY_ENDPOINT" != "" ]; then
  echo "{\"is_owner\": \"false\", \"id\": \"$ANY_ENDPOINT\"}"
else
  echo "{\"is_owner\": \"false\", \"id\": \"None\"}"
fi
