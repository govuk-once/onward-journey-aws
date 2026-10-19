#!/bin/bash

# This script is used to test the logging of AWS Lambda functions. For now, it only prints logs to the console as a visual check, but it could be extended or modified to test routing and/or act as a pass/fail test in the future.

show_help() {
  cat << EOF
Usage: $(basename "$0") [OPTIONS] [FUNCTION_NAME...]

Test logging for AWS Lambda functions in the current Terraform workspace.

Options:
  --include-stateful    Invoke both stateless and stateful lambdas.
  -h, --help            Show this help message and exit.

Arguments:
  FUNCTION_NAME         One or more specific lambda function names to invoke.
                        If provided, only these functions will be executed.

Examples:
  $(basename "$0")                                      # Run all stateless lambdas (default)
  $(basename "$0") --include-stateful                   # Run all stateless and stateful lambdas
  $(basename "$0") <dev workspace>-client-ws-router     # Run a single specific lambda in specified <dev workspace>
  $(basename "$0") func-one func-two                    # Run multiple specific lambdas (must append workspace prefix to function names)
EOF
}

# Determine the script directory and current Terraform workspace
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
workspace=$(terraform -chdir="$SCRIPT_DIR/../infrastructure/services" workspace show)

stateless_lambdas=(
  "${workspace}-client-ws-router"
  "${workspace}-crm-wh-router"
  "${workspace}-crm-contentguru-wh-authorizer"
  "${workspace}-crm-tool"
  "${workspace}-kb-sync-check-kb-meta"
  "${workspace}-kb-sync-check-sync-meta"
  "${workspace}-kb-sync-fetch-articles"
  "${workspace}-rds-tool"
)

stateful_lambdas=(
  "${workspace}-rds-init"
  "${workspace}-rds-seeder"
  "${workspace}-kb-sync-upsert"
  "${workspace}-kb-sync-update-sync-meta"
  "${workspace}-kb-sync-cleanup-unmapped"
)

include_stateful=false
specific_functions=()

# Parse command line arguments
for arg in "$@"; do
  case "$arg" in
    -h|--help)
      show_help
      exit 0
      ;;
    --include-stateful)
      include_stateful=true
      ;;
    *)
      specific_functions+=("$arg")
      ;;
  esac
done

# Determine target functions to invoke
targets=()

if [[ ${#specific_functions[@]} -gt 0 ]]; then
  # If specific function names were provided, run those only
  targets=("${specific_functions[@]}")
else
  # Default execution
  targets=("${stateless_lambdas[@]}")
  if [[ "$include_stateful" == true ]]; then
    targets+=("${stateful_lambdas[@]}")
  fi
fi

tested_count=0

# Execute AWS Lambda invocations
for function in "${targets[@]}"; do
  echo " "
  echo "----------------invoking $function----------------"
  echo " "
  aws lambda invoke \
    --function-name "$function" \
    --payload '{"message": "dummy event to test error/info logging"}' \
    --cli-binary-format raw-in-base64-out \
    --log-type Tail \
    /dev/null | jq -r '.LogResult' | base64 --decode
  tested_count=$((tested_count + 1))
done

echo " ---------------------------------------------------"
echo "Tested logging for $tested_count Lambda function(s)."