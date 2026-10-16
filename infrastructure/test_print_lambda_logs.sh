#!/bin/bash

functions=(
  # "jr1-client-ws-router"
  # "jr1-crm-wh-router"
  # "jr1-crm-contentguru-wh-authorizer"
  "jr1-crm-tool"
  "jr1-kb-sync-check-kb-meta"
  "jr1-kb-sync-check-sync-meta"
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