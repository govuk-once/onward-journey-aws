import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import boto3
from botocore.config import Config

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

boto_cfg = Config(retries={"max_attempts": 3, "mode": "standard"})
secrets_client = boto3.client(
    "secretsmanager", region_name="eu-west-2", config=boto_cfg
)
cloudwatch_client = boto3.client("cloudwatch", region_name="eu-west-2", config=boto_cfg)
logs_client = boto3.client("logs", region_name="eu-west-2", config=boto_cfg)

CACHED_WEBHOOK_URL = None
TAGS_CACHE: dict[str, dict[str, str]] = {}


def get_webhook_url(force_refresh: bool = False) -> str:
    global CACHED_WEBHOOK_URL
    if CACHED_WEBHOOK_URL and not force_refresh:
        return CACHED_WEBHOOK_URL

    secret_arn = os.environ["SLACK_WEBHOOK_SECRET_ARN"]
    response = secrets_client.get_secret_value(SecretId=secret_arn)
    secret_data = json.loads(response["SecretString"])
    CACHED_WEBHOOK_URL = secret_data["webhook_url"]
    return CACHED_WEBHOOK_URL


def get_alarm_tags(alarm_arn: str) -> dict[str, str]:
    if not alarm_arn:
        return {}
    if alarm_arn in TAGS_CACHE:
        return TAGS_CACHE[alarm_arn]
    try:
        response = cloudwatch_client.list_tags_for_resource(ResourceARN=alarm_arn)
        tags = {tag["Key"]: tag["Value"] for tag in response.get("Tags", [])}
        TAGS_CACHE[alarm_arn] = tags
        return tags
    except Exception as exc:
        logger.warning(f"Could not retrieve tags for alarm {alarm_arn}: {exc}")
        return {}


def extract_alarm_data(record_payload: dict, default_env: str) -> dict:
    """Normalises SNS CloudWatch Alarm payloads into a consistent dictionary."""
    alarm_name = record_payload.get("AlarmName", "Unknown Alarm")
    trigger = record_payload.get("Trigger", {})

    dim_map = {}

    def parse_dims(dims):
        for d in dims:
            if isinstance(d, dict) and (d.get("name") or d.get("Name")):
                dim_map[d.get("name") or d.get("Name")] = d.get("value") or d.get(
                    "Value"
                )

    parse_dims(trigger.get("Dimensions") or [])
    for m in trigger.get("Metrics") or []:
        parse_dims(m.get("MetricStat", {}).get("Metric", {}).get("Dimensions") or [])

    namespace = trigger.get("Namespace") or next(
        (
            m.get("MetricStat", {}).get("Metric", {}).get("Namespace")
            for m in trigger.get("Metrics") or []
            if m.get("MetricStat", {}).get("Metric", {}).get("Namespace")
        ),
        "",
    )
    tags = get_alarm_tags(record_payload.get("AlarmArn", ""))

    return {
        "alarm_name": alarm_name,
        "alarm_desc": record_payload.get("AlarmDescription", ""),
        "new_state": record_payload.get("NewStateValue", "ALARM"),
        "reason": record_payload.get("NewStateReason", "No reason provided"),
        "timestamp": record_payload.get("StateChangeTime", "N/A"),
        "account_id": record_payload.get("AWSAccountId", ""),
        "region": "eu-west-2",
        "dim_map": dim_map,
        "namespace": namespace,
        "env": tags.get("Environment")
        or tags.get("environment")
        or dim_map.get("FunctionName", "").split("-")[0]
        or alarm_name.split("-")[0]
        or default_env,
        "component": tags.get("Component") or dim_map.get("FunctionName") or "Unknown",
    }


def get_search_terms(alarm_name: str) -> list[str]:
    """
    Builds prioritised search terms.
    Preserves the full prefix first (e.g. 'sw5-kb-sync') to avoid matching identically
    named services in other workspaces, falling back to stripped names for shared infra.
    """
    clean_full = alarm_name
    noise_suffixes = [
        "-lambdas-logged-error",
        "-logged-error",
        "-lambdas",
        "-errors",
        "-error",
        "-alarm",
        "-high",
        "-low",
    ]
    for suffix in noise_suffixes:
        if clean_full.endswith(suffix):
            clean_full = clean_full[: -len(suffix)]
            break

    search_terms = [clean_full]
    if "-" in clean_full:
        search_terms.append(clean_full.split("-", 1)[1])
    return search_terms


def is_actual_error(message: str) -> bool:
    """Filters out log events where error tokens appear inside warning messages."""
    msg_upper = message.upper()
    if '"LOG_LEVEL": "WARNING"' in msg_upper or '"LEVEL": "WARNING"' in msg_upper:
        return False
    if "[WARNING]" in msg_upper or "WARN:" in msg_upper:
        return False
    return "ERROR" in msg_upper or "EXCEPTION" in msg_upper


def resolve_infra_links_and_snippets(data: dict) -> tuple[list[str], str | None]:
    """
    Identifies the underlying AWS resource and returns relevant console deep links.
    For log-based alarms, it retrieves the specific error trace that triggered the alarm.
    """
    links = []
    snippet = None
    region = data["region"]
    desc = data["alarm_desc"]
    dim_map = data["dim_map"]
    namespace = data["namespace"]

    encoded_alarm = urllib.parse.quote(data["alarm_name"])
    links.append(
        f"<https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}#alarmsV2:alarm/{encoded_alarm}|View Alarm>"
    )

    # Favour explicit identifiers provided in the alarm description by Terraform
    explicit_log = re.search(r"(/aws/[a-zA-Z0-9_./-]+)", desc)
    explicit_nat = re.search(r"(nat-[0-9a-f]+)", desc)
    explicit_sqs = re.search(
        r"(https://sqs.[a-z0-9-]+.amazonaws.com/[0-9]+/[a-zA-Z0-9_-]+)", desc
    )

    nat_id = explicit_nat.group(1) if explicit_nat else dim_map.get("NatGatewayId")
    if nat_id or namespace == "AWS/NATGateway":
        if nat_id:
            links.append(
                f"<https://{region}.console.aws.amazon.com/vpcconsole/home?region={region}#NatGatewayDetails:natGatewayId={nat_id}|NAT Gateway ({nat_id})>"
            )
        else:
            links.append(
                f"<https://{region}.console.aws.amazon.com/vpcconsole/home?region={region}#NatGateways:|NAT Gateways>"
            )
        return links, None

    queue_url = explicit_sqs.group(1) if explicit_sqs else None
    queue_name = dim_map.get("QueueName")
    if queue_url or queue_name or namespace == "AWS/SQS":
        q_label = queue_name or "Queue"
        links.append(
            f"<https://{region}.console.aws.amazon.com/sqs/v3/home?region={region}#/queues|SQS: {q_label}>"
        )
        return links, None

    # Discover log groups dynamically if an explicit dimension isn't provided
    log_groups = []
    if explicit_log:
        log_groups.append(explicit_log.group(1))
    elif "LogGroupName" in dim_map:
        log_groups.append(dim_map["LogGroupName"])
    elif "FunctionName" in dim_map:
        log_groups.append(f"/aws/lambda/{dim_map['FunctionName']}")
    else:
        search_terms = get_search_terms(data["alarm_name"])
        try:
            all_lambda_groups = []
            paginator = logs_client.get_paginator("describe_log_groups")
            for page in paginator.paginate(logGroupNamePrefix="/aws/lambda/"):
                for group in page.get("logGroups", []):
                    all_lambda_groups.append(group["logGroupName"])

            # Match on prioritised terms (exact workspace/env prefix evaluated first)
            for term in search_terms:
                matched = [name for name in all_lambda_groups if term in name]
                if matched:
                    log_groups = matched
                    break
        except Exception as exc:
            logger.warning(f"Log group discovery failed: {exc}")

    # Fetch recent error events across discovered groups (30-minute lookback)
    culprit_group = None
    if log_groups:
        start_time_ms = int((time.time() - 1800) * 1000)
        for lg in log_groups[:8]:
            try:
                # Query strictly for error markers, avoiding loose matches on warnings
                response = logs_client.filter_log_events(
                    logGroupName=lg,
                    filterPattern='?ERROR ?Exception ?"[ERROR]"',
                    startTime=start_time_ms,
                    limit=10,
                )
                raw_events = response.get("events", [])
                filtered_messages = [
                    e["message"].strip()
                    for e in raw_events
                    if is_actual_error(e["message"])
                ]

                if filtered_messages:
                    messages = filtered_messages[-3:]
                    short_name = lg.split("/")[-1]
                    snippet = f"Source: {short_name}\n\n" + "\n\n---\n\n".join(messages)
                    culprit_group = lg
                    break
            except Exception as exc:
                logger.warning(f"Could not fetch snippet for {lg}: {exc}")
                continue

    target_group = culprit_group or (log_groups[0] if len(log_groups) == 1 else None)
    if target_group:
        encoded = urllib.parse.quote(target_group, safe="").replace("%", "$25")
        clean_label = target_group.split("/")[-1]
        links.append(
            f"<https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}#logsV2:log-groups/log-group/{encoded}|Log Group: {clean_label}>"
        )
    else:
        links.append(
            f"<https://{region}.console.aws.amazon.com/cloudwatch/home?region={region}#logsV2:log-groups|CloudWatch Logs>"
        )

    return links, snippet


def build_slack_payload(data: dict, links: list[str], snippet: str | None) -> dict:
    if data["account_id"]:
        links.append(f"Account: `{data['account_id']}`")

    color = "#E01E5A" if data["new_state"] == "ALARM" else "#2EB886"
    header_raw = f"🚨 [{data['env'].upper()}] CloudWatch Alarm: {data['alarm_name']}"
    header_text = (header_raw[:147] + "...") if len(header_raw) > 150 else header_raw

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": header_text, "emoji": True},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Status:*\n`{data['new_state']}`"},
                {"type": "mrkdwn", "text": f"*Environment:*\n{data['env']}"},
                {"type": "mrkdwn", "text": f"*Component:*\n{data['component']}"},
                {"type": "mrkdwn", "text": f"*Time:*\n{data['timestamp']}"},
            ],
        },
    ]

    if data["alarm_desc"]:
        desc = data["alarm_desc"]
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Description:*\n_{(desc[:250] + '...') if len(desc) > 250 else desc}_",
                },
            }
        )

    if snippet:
        sanitized = snippet.replace("```", "'''")[:1800]
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*Recent Errors (up to 3 found in the 30 mins prior to processing):*\n"
                        "_Note: If alert delivery was delayed, these may not be the exact errors that triggered the alarm._\n"
                        f"```{sanitized}```"
                    ),
                },
            }
        )
    else:
        sanitized = data["reason"].replace("```", "'''")[:1000]
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Alarm Trigger Reason:*\n```{sanitized}```",
                },
            }
        )

    blocks.append(
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"🔗 {' | '.join(links)}"}],
        }
    )

    preview_text = f"[{data['env'].upper()}] {data['new_state']}: {data['alarm_name']}"

    return {
        "attachments": [
            {
                "color": color,
                # Explicitly pass preview_text to fallback for OS push notifications
                # Omit the top-level 'text' property to prevent duplicate headers in the channel
                "fallback": preview_text,
                "blocks": blocks,
            }
        ]
    }


def post_to_slack(webhook_url: str, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            logger.info(f"Slack post success: status {res.status}")
    except urllib.error.HTTPError as err:
        error_body = err.read().decode("utf-8", errors="replace")
        logger.error(f"Slack API rejected payload (HTTP {err.code}): {error_body}")
        if err.code in (403, 404):
            global CACHED_WEBHOOK_URL
            CACHED_WEBHOOK_URL = None
        raise
    except Exception as exc:
        logger.error(f"Transport failure reaching Slack: {exc}")
        raise


def lambda_handler(event, context):
    default_env = os.environ.get("ENVIRONMENT", "Unknown")
    records = event.get("Records", [])

    if not records:
        return {"statusCode": 200, "body": "No records"}

    webhook_url = get_webhook_url()
    failed_count = 0

    for record in records:
        raw_message = record.get("Sns", {}).get("Message", "{}")
        try:
            payload = json.loads(raw_message)
        except json.JSONDecodeError:
            payload = {"AlarmName": "Raw SNS Message", "NewStateReason": raw_message}

        try:
            alarm_data = extract_alarm_data(payload, default_env)
            links, snippet = resolve_infra_links_and_snippets(alarm_data)
            slack_body = build_slack_payload(alarm_data, links, snippet)

            post_to_slack(webhook_url, slack_body)
        except Exception as exc:
            logger.error(f"Failed to process alert: {exc}", exc_info=True)
            failed_count += 1

    if failed_count > 0:
        raise RuntimeError(
            f"{failed_count}/{len(records)} alerts failed to post to Slack"
        )

    return {"statusCode": 200, "body": "Alerts processed"}
