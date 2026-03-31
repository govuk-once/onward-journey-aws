#!/bin/bash

# 1. Path to your Terraform config (adjust if your folder name is different)
TFVARS_FILE="infrastructure/local.auto.tfvars"

# 2. Extract the environment value
# Looks for a line starting with 'environment', grabs the part after '=',
# and strips quotes and whitespace.
ENV_PREFIX=$(grep '^environment' "$TFVARS_FILE" | awk -F'=' '{print $2}' | tr -d ' "')

# 3. THE "FAIL-FAST" CHECK
# If the variable is empty, the script will stop immediately.
if [ -z "$ENV_PREFIX" ]; then
    echo "ERROR: Could not find 'environment' defined in $TFVARS_FILE"
    echo "Please ensure your local.auto.tfvars is configured before running tests."
    exit 1
fi

ORCHESTRATOR_NAME="${ENV_PREFIX}-orchestrator"
EVENT_FILE="tests/test_event.json"
RESPONSE_FILE="tests/response.json"

echo "Environment Detected: $ENV_PREFIX"
echo "Triggering $ORCHESTRATOR_NAME..."

aws lambda invoke \
    --function-name "$ORCHESTRATOR_NAME" \
    --payload fileb://"$EVENT_FILE" \
    --cli-binary-format raw-in-base64-out \
    "$RESPONSE_FILE"

echo "Test Complete. Response from Orchestrator:"
cat "$RESPONSE_FILE" | jq .
