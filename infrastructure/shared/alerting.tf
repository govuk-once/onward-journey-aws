# Central SNS topic for all account error notifications
resource "aws_sns_topic" "slack_alerts" {
  name = "oj-aws-errors"

  tags = {
    Component = "alerting"
  }
}

# -----------------------------------------------------------------------------
# Dead Letter Queue
# -----------------------------------------------------------------------------

# Retains alerts dropped due to Lambda execution failures or Slack rate-limits
resource "aws_sqs_queue" "slack_notifier_dlq" {
  name                      = "shared-slack-notifier-dlq"
  message_retention_seconds = 604800 # 7 days

  tags = {
    Component = "alerting"
  }
}

# Permits SNS to route failed delivery attempts directly to the DLQ
resource "aws_sqs_queue_policy" "slack_notifier_dlq_policy" {
  queue_url = aws_sqs_queue.slack_notifier_dlq.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "sns.amazonaws.com" }
        Action    = "sqs:SendMessage"
        Resource  = aws_sqs_queue.slack_notifier_dlq.arn
        Condition = {
          ArnEquals = {
            "aws:SourceArn" = aws_sns_topic.slack_alerts.arn
          }
        }
      }
    ]
  })
}

# Monitors if the alerting pipeline itself drops messages into the DLQ
resource "aws_cloudwatch_metric_alarm" "dlq_messages_alarm" {
  alarm_name          = "shared-slack-notifier-dlq-messages"
  alarm_description   = "Alerts if dropped messages accumulate in the Slack notifier DLQ"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 60
  statistic           = "Sum"
  threshold           = 0

  dimensions = {
    QueueName = aws_sqs_queue.slack_notifier_dlq.name
  }

  tags = {
    Component = "alerting"
  }
}

# -----------------------------------------------------------------------------
# Slack Notifier Lambda & Subscription
# -----------------------------------------------------------------------------

module "slack_notifier_lambda" {
  source = "../modules/lambda"

  environment   = "shared"
  function_name = "slack-notifier"
  description   = "Forwards CloudWatch and SNS alerts to Slack Webhook"
  handler       = "handler.lambda_handler"
  runtime       = "python3.13"
  timeout       = 15
  source_dir    = "slack_notifier"

  environment_variables = {
    ENVIRONMENT              = "shared"
    SLACK_WEBHOOK_SECRET_ARN = aws_secretsmanager_secret.slack_webhook.arn
    LOG_LEVEL                = "INFO"
  }

  tags = {
    Component = "alerting"
  }
}

# Directs failed async Lambda retries to the DLQ
resource "aws_lambda_function_event_invoke_config" "slack_notifier_async_config" {
  function_name          = module.slack_notifier_lambda.function_name
  maximum_retry_attempts = 2

  destination_config {
    on_failure {
      destination = aws_sqs_queue.slack_notifier_dlq.arn
    }
  }
}

# Authorises SNS to invoke the Lambda function
resource "aws_lambda_permission" "sns_alerts" {
  statement_id  = "AllowExecutionFromSNS"
  action        = "lambda:InvokeFunction"
  function_name = module.slack_notifier_lambda.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.slack_alerts.arn
}

# Subscribes Lambda to SNS with a DLQ redrive fallback
resource "aws_sns_topic_subscription" "slack_notifier" {
  topic_arn = aws_sns_topic.slack_alerts.arn
  protocol  = "lambda"
  endpoint  = module.slack_notifier_lambda.function_arn

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.slack_notifier_dlq.arn
  })

  depends_on = [aws_sqs_queue_policy.slack_notifier_dlq_policy]
}

# -----------------------------------------------------------------------------
# IAM Execution Permissions
# -----------------------------------------------------------------------------

resource "aws_iam_policy" "slack_notifier_access" {
  name        = "shared-slack-notifier-policy"
  description = "Allows Slack notifier Lambda to read webhook secret, alarm tags, and write to DLQ"

  tags = {
    Component = "alerting"
  }

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "secretsmanager:GetSecretValue"
        Resource = aws_secretsmanager_secret.slack_webhook.arn
      },
      {
        Effect   = "Allow"
        Action   = "sqs:SendMessage"
        Resource = aws_sqs_queue.slack_notifier_dlq.arn
      },
      {
        Effect   = "Allow"
        Action   = "cloudwatch:ListTagsForResource"
        Resource = "arn:aws:cloudwatch:eu-west-2:*:alarm:*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:DescribeLogGroups",
          "logs:FilterLogEvents"
        ]
        # Allow access to log groups and their underlying log streams
        Resource = "arn:aws:logs:eu-west-2:*:*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "slack_notifier_policy_attach" {
  role       = module.slack_notifier_lambda.role_name
  policy_arn = aws_iam_policy.slack_notifier_access.arn
}

# -----------------------------------------------------------------------------
# Secrets Manager
# -----------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "slack_webhook" {
  name                    = "shared-slack-webhook"
  description             = "Slack Incoming Webhook URL for error alerting"
  recovery_window_in_days = 0

  tags = {
    Component = "alerting"
  }
}

resource "aws_secretsmanager_secret_version" "slack_webhook_placeholder" {
  secret_id     = aws_secretsmanager_secret.slack_webhook.id
  secret_string = jsonencode({ webhook_url = "PLACEHOLDER_SET_IN_CONSOLE" })

  lifecycle {
    ignore_changes = [
      secret_string
    ]
  }
}
