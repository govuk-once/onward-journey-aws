#!/bin/bash

workspace=$(terraform workspace show)

functions=(
  # "${workspace}-client-ws-router"
  # "${workspace}-crm-wh-router"
  # "${workspace}-crm-contentguru-wh-authorizer"
  "${workspace}-crm-tool"
  "${workspace}-kb-sync-check-kb-meta"
  "${workspace}-kb-sync-check-sync-meta"
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