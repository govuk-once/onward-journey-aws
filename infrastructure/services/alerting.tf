# DEPENDENCY: slack-alerting shared infrastructure (infrastructure/slack-alerting) must be applied before this workspace
data "aws_sns_topic" "lambda_errors_topic" {
  name = "lambda-errors-topic"
}

locals {
  sns_topic_arn = data.aws_sns_topic.lambda_errors_topic.arn
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
  alarm_actions             = [local.sns_topic_arn]

  dimensions = {
    FunctionName = each.key
  }
}
