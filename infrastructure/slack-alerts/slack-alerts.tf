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
  iam_role_arn       = aws_iam_role.amazon_q_notification_role.arn

  # require channel members to authenticate and choose a user role to execute queries.
  user_authorization_required = true

  # User permissions are restricted by the guardrail policy, regardless of which role they select when they authenticate.
  guardrail_policy_arns = ["arn:aws:iam::aws:policy/CloudWatchReadOnlyAccess"]

  sns_topic_arns = [aws_sns_topic.oj_aws_errors.arn]
  logging_level  = "ERROR"
}

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

# Channel role policy - allows Amazon Q to populate Slack alerts with metric data from CloudWatch. Blocks command execution for unauthenticated channel members
resource "aws_iam_policy" "amazon_q_notification_policy" {
  name        = "AWS-Chatbot-NotificationsOnly-Policy"
  description = "Allows AWS Chatbot to fetch metrics and render notifications"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "cloudwatch:Describe*",
          "cloudwatch:Get*",
          "cloudwatch:List*"
        ]
        Effect   = "Allow"
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "amazon_q_notification_policy" {
  role       = aws_iam_role.amazon_q_notification_role.name
  policy_arn = aws_iam_policy.amazon_q_notification_policy.arn
}

output "sns_topic_arn" {
  value       = aws_sns_topic.oj_aws_errors.arn
  description = "SNS topic ARN"
}
