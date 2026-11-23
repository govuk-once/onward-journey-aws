#!/usr/bin/env bash
set -euo pipefail
set +x # Ensure credentials are never echoed in trace logs

trap 'unset SECRET_JSON CREDS_B64' EXIT

TARGETS_FILE="password-rotation-targets.json"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-eu-west-2}"

if [[ ! -f "$TARGETS_FILE" ]]; then
  echo "Error: $TARGETS_FILE not found in $(pwd)." >&2
  exit 1
fi

# Parse, validate, and deduplicate target indices (e.g. "tester-1" -> "1")
RAW_INDICES=$(python3 -c '
import json, re, sys
with open(sys.argv[1]) as f:
    targets = json.load(f)
if not isinstance(targets, list):
    sys.exit("Error: password-rotation-targets.json must be a JSON array.")
indices = []
for t in targets:
    m = re.fullmatch(r"tester-([1-9][0-9]*)", str(t))
    if not m:
        sys.exit(f"Error: Invalid target \"{t}\". Expected format: \"tester-1\".")
    indices.append(m.group(1))
for idx in dict.fromkeys(indices):
    print(idx)
' "$TARGETS_FILE")

INDICES=()
while IFS= read -r idx; do
  if [[ -n "$idx" ]]; then
    INDICES+=("$idx")
  fi
done <<< "$RAW_INDICES"

if [[ ${#INDICES[@]} -eq 0 ]]; then
  echo "No targets listed in $TARGETS_FILE. Nothing to rotate."
  exit 0
fi

# Build the -replace arguments for Terraform
REPLACE_ARGS=()
for idx in "${INDICES[@]}"; do
  REPLACE_ARGS+=("-replace=random_password.tester_passwords[\"${idx}\"]")
done

echo "Step 1/2: Rotating passwords in Terraform state and Secrets Manager..."
terraform apply "${REPLACE_ARGS[@]}"

echo "Step 2/2: Syncing new credentials to targeted AWS Amplify branches..."
APP_ID=$(terraform output -raw amplify_app_id)
APP_NAME=$(aws amplify get-app --app-id "$APP_ID" --query 'app.name' --output text)
ENV_NAME="${APP_NAME%-onward-journey-frontend}"

if [[ "$ENV_NAME" == "$APP_NAME" ]]; then
  echo "Error: Unexpected Amplify app name format: $APP_NAME" >&2
  exit 1
fi

SECRET_JSON=$(aws secretsmanager get-secret-value \
  --secret-id "${ENV_NAME}/frontend/tester-credentials" \
  --query SecretString \
  --output text)

BRANCHES_JSON=$(aws amplify list-branches --app-id "$APP_ID" --output json)

for idx in "${INDICES[@]}"; do
  BRANCH_NAME=$(python3 -c '
import json, sys
idx = sys.argv[1]
branches = json.load(sys.stdin).get("branches", [])
matches = [b["branchName"] for b in branches if b["branchName"].startswith(f"tester-{idx}-")]
if len(matches) == 0:
    sys.exit(f"Error: Could not find Amplify branch for tester-{idx}")
if len(matches) > 1:
    sys.exit(f"Error: Multiple Amplify branches matched tester-{idx}: {matches}")
print(matches[0])
' "$idx" <<< "$BRANCHES_JSON")

  CREDS_B64=$(python3 -c '
import base64, json, sys
idx = sys.argv[1]
data = json.load(sys.stdin)
entry = data.get(f"Tester {idx}")
if not entry:
    sys.exit(f"Error: Could not find Tester {idx} in Secrets Manager")
user, pwd = entry["username"], entry["password"]
raw = f"{user}:{pwd}".encode("utf-8")
print(base64.b64encode(raw).decode("utf-8"))
' "$idx" <<< "$SECRET_JSON")

  aws amplify update-branch \
    --app-id "$APP_ID" \
    --branch-name "$BRANCH_NAME" \
    --enable-basic-auth \
    --basic-auth-credentials "$CREDS_B64" \
    > /dev/null

  echo "  Updated Basic Auth for $BRANCH_NAME (Tester $idx)"
done

echo "Password rotation complete. Secrets Manager and Amplify are in sync."
