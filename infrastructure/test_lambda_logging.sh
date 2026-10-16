#!/bin/bash

# This script is used to test the logging of AWS Lambda functions.
# For now, it only prints logs to the console as a visual check, but it could be extended or modified to test routing and/or act as a pass/fail test in the future.

workspace=$(terraform workspace show)

functions=(
  # "${workspace}-client-ws-router"
  # "${workspace}-crm-wh-router"
  # "${workspace}-crm-contentguru-wh-authorizer"
  # "${workspace}-crm-tool"
  "${workspace}-kb-sync-check-kb-meta"
  # "${workspace}-kb-sync-check-sync-meta"
  # "${workspace}-kb-sync-fetch-articles"
  # "${workspace}-kb-sync-upsert"
  # "${workspace}-kb-sync-update-sync-meta"
  # "${workspace}-kb-sync-cleanup-unmapped"
  "${workspace}-rds-init"
  "${workspace}-rds-seeder"
  "${workspace}-rds-tool"
)

for function in "${functions[@]}"; do
  echo " "
  echo "----------------invoking $function----------------"
  echo " "
  aws lambda invoke \
    --function-name $function \
    --payload '{"event": "ping"}' \
    --cli-binary-format raw-in-base64-out \
    --log-type Tail \
    /dev/null | jq -r '.LogResult' | base64 --decode
done