import json
import logging
import os

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))


def lambda_handler(event: dict, context: object) -> dict:
    request_id = getattr(context, "aws_request_id", "unknown")

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
