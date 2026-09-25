import json
import logging
import os

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))


def lambda_handler(event: dict, lambda_context: object) -> dict:
    # Fail fast if lambda_context does not satisfy the AWS Lambda runtime contract
    request_id = lambda_context.aws_request_id

    # Auditability: Tie the execution context to the log stream
    logger.info("Ingesting CRM webhook payload", extra={"request_id": request_id})

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        logger.error("Failed to parse JSON body", extra={"request_id": request_id})
        return {
            "statusCode": 400,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"error": "Invalid JSON body"}),
        }

    # TODO(JOUR-252): Subtask 5 - Implement DynamoDB lookup and WebSocket push logic here

    # Content Guru Storm expects an immediate 200 OK acknowledgment
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"status": "acknowledged", "request_id": request_id}),
    }
