resource "aws_sns_topic" "oj_aws_errors" {
  name = "oj-aws-errors"
}

variable "slack_workspace_id" {
  type        = string
  description = "ID of the Slack workspace that will receive alerts"
}

variable "slack_channel_id" {
  type        = string
  description = "ID of the Slack channel that will receive alerts"
}

resource "aws_chatbot_slack_channel_configuration" "oj_aws_errors" {

  configuration_name = "oj-aws-errors-config"
  slack_channel_id   = var.slack_channel_id
  slack_team_id      = var.slack_workspace_id

  iam_role_arn = aws_iam_role.amazon_q_notification_role.arn
  # require channel members to authenticate and choose a user role to execute queries.
  user_authorization_required = true
  # User permissions are restricted by the guardrail policy, regardless of which role they select when they authenticate.
  guardrail_policy_arns = ["arn:aws:iam::aws:policy/CloudWatchReadOnlyAccess"]

  sns_topic_arns = [aws_sns_topic.oj_aws_errors.arn]
  logging_level  = "INFO"
}

# Channel role - allows Amazon Q to populate Slack alerts with metric data from CloudWatch. Blocks command execution for unauthenticated channel members
resource "aws_iam_role" "amazon_q_notification_role" {
  name = "amazon_q_notification_role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Sid    = ""
        Principal = {
          Service = "chatbot.amazonaws.com"
        }
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "amazon_q_notifications_only" {
  role       = aws_iam_role.amazon_q_notification_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWS-Chatbot-NotificationsOnly-Policy"
}

output "sns_topic_arn" {
  value       = aws_sns_topic.oj_aws_errors.arn
  description = "SNS topic ARN"
}
