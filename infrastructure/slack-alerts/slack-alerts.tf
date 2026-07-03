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
  iam_role_arn       = aws_iam_role.amazon_q.arn
  slack_channel_id   = var.slack_channel_id
  slack_team_id      = var.slack_workspace_id
  sns_topic_arns     = [aws_sns_topic.oj_aws_errors.arn]
  logging_level      = "INFO"
}

# Amazon Q Role
# Allows Amazon Q Developer in chat applications to read Cloudwatch logs
resource "aws_iam_role" "amazon_q" {
  name = "amazon_q_role"
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

resource "aws_iam_role_policy_attachment" "amazon_q_cloudwatch_access" {
  role       = aws_iam_role.amazon_q.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchReadOnlyAccess"
}

resource "aws_iam_role_policy_attachment" "amazon_q_logs_access" {
  role       = aws_iam_role.amazon_q.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchLogsFullAccess"
}

output "sns_topic_arn" {
  value       = aws_sns_topic.oj_aws_errors.arn
  description = "SNS topic ARN"
}
