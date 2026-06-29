resource "aws_sns_topic" "lambda_errors" {
  name = "${var.environment}-lambda-errors-topic"
}


locals {
  lambda_function_names = [
    "${var.environment}-kb-sync-fetch-articles",
    "${var.environment}-rds-seeder",
    "${var.environment}-kb-sync-upsert",
    "${var.environment}-kb-sync-check-sync-meta",
    "${var.environment}-kb-sync-update-check-meta",
    "${var.environment}-crm-tool",
    "${var.environment}-rds-tool",
    "${var.environment}-kb-sync-check-kb-meta",
    "${var.environment}-rds-init",
    "${var.environment}-orchestrator"
  ]
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each = toset(local.lambda_function_names)

  alarm_name                = "${var.environment}_lambda_errors_${each.key}"
  comparison_operator       = "GreaterThanOrEqualToThreshold"
  evaluation_periods        = 1
  metric_name               = "Errors"
  namespace                 = "AWS/Lambda"
  period                    = 60
  statistic                 = "Sum"
  threshold                 = 1
  alarm_description         = "Triggers if ${each.key} experiences 1 or more errors in a 1-minute window."
  insufficient_data_actions = []
  alarm_actions             = [aws_sns_topic.lambda_errors.arn]

  dimensions = {
    FunctionName = each.key
  }
}

data "aws_chatbot_slack_workspace" "gds_oj_slack" {
  slack_team_name = "GDS"
}

resource "aws_chatbot_slack_channel_configuration" "oj_aws_errors" {
  configuration_name = "${var.environment}-oj-aws-errors-config"
  iam_role_arn       = aws_iam_role.amazon_q.arn
  slack_channel_id   = "C0BD43H6J6N"
  slack_team_id      = data.aws_chatbot_slack_workspace.gds_oj_slack.slack_team_id
  sns_topic_arns     = [aws_sns_topic.lambda_errors.arn]
}
