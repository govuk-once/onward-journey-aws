#!/bin/bash

# 1. Path to your Terraform config
TFVARS_FILE="infrastructure/services/local.auto.tfvars"

EVENT_FILE="tests/test_event.json"
RESPONSE_FILE="tests/response.json"

if [ -n "${AWS_REGION:-${AWS_DEFAULT_REGION}}" ]; then
    REGION="${AWS_REGION:-${AWS_DEFAULT_REGION}}"
    echo "[REGION LOG] Source: Environment Variable (AWS_REGION/AWS_DEFAULT_REGION) -> '$REGION'"
else
    REGION="eu-west-2"
    echo "[REGION LOG] Source: Fallback Default -> '$REGION'"
fi

# 2. Extract the environment value
ENV_PREFIX=$(grep '^environment' "$TFVARS_FILE" | awk -F'=' '{print $2}' | tr -d ' "')

# 3. Fail-fast check
if [ -z "$ENV_PREFIX" ]; then
    echo "ERROR: Could not find 'environment' defined in $TFVARS_FILE"
    echo "Please ensure your local.auto.tfvars is configured before running tests."
    exit 1
fi

# 4. Fetch the API Gateway WSS Endpoint
# Adjust the 'API_NAME' search string to match your Terraform naming convention
API_NAME="${ENV_PREFIX}-client-ws-gw"
echo "[System] Looking up API Gateway ID for: $API_NAME..."

API_ID=$(aws apigatewayv2 get-apis \
    --query "Items[?contains(Name, '$API_NAME')].ApiId" \
    --output text)

if [ -z "$API_ID" ] || [ "$API_ID" == "None" ]; then
    echo "ERROR: Could not find a WebSocket API matching name: $API_NAME"
    exit 1
fi

WSS_URL="wss://${API_ID}.execute-api.${REGION}.amazonaws.com/${ENV_PREFIX}/"

echo "Environment Detected: $ENV_PREFIX"
echo "Triggering AgentCore via WSS: $WSS_URL"

# 5. Invoke via WebSocket and stream the response
> "$RESPONSE_FILE"
echo "[System] Streaming response from AgentCore:"

# Start a valid JSON array
printf "[\n" > "$RESPONSE_FILE"

# Pipe wscat output into a line-by-line reader
wscat -c "$WSS_URL" -w 60 -x "$(cat "$EVENT_FILE")" | (
    FIRST_LINE=true
    while IFS= read -r line; do

        # 5.1 Skip outgoing payloads echoed by wscat
        if [[ "$line" == *"> "* ]]; then
            continue
        fi

        # 5.2 Skip any connection/disconnection text (must contain '{')
        if [[ ! "$line" == *\{* ]]; then
            continue
        fi

        # 5.3 Extract ONLY the JSON part (throws away ANSI colors, '< ', spaces)
        CLEAN_LINE="{${line#*\{}"

        # Safely extract the 'text' string if the type is 'chunk'.
        # The -r flag removes the surrounding quotes and unescapes formatting (like \n).
        TEXT_CHUNK=$(echo "$CLEAN_LINE" | jq -r 'if .type == "chunk" and .text != null then .text else "" end' 2>/dev/null)

        # Print directly to the terminal
        printf "%s" "$TEXT_CHUNK"

        # Format and append the raw json to the response file array
        if [ "$FIRST_LINE" = true ]; then
            printf "  %s" "$CLEAN_LINE" >> "$RESPONSE_FILE"
            FIRST_LINE=false
        else
            printf ",\n  %s" "$CLEAN_LINE" >> "$RESPONSE_FILE"
        fi

        # 5.4 Check for the specific 'done' JSON block
        COMPACT_LINE=$(echo "$CLEAN_LINE" | tr -d ' ' | tr -d '\n')
        if [[ "$COMPACT_LINE" == *"\"type\":\"done\""* ]]; then
            echo -e "\n\n[System] Received 'done' signal. Closing connection."

            # Forcefully kill the wscat connection
            pkill -9 -f "wscat -c" 2>/dev/null
            break
        fi
    done
)

# Close the JSON array
printf "\n]\n" >> "$RESPONSE_FILE"

echo "[System] Test Complete. Valid JSON array saved to $RESPONSE_FILE."
