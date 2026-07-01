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
    "${var.environment}-kb-sync-update-sync-meta",
    "${var.environment}-crm-tool",
    "${var.environment}-rds-tool",
    "${var.environment}-kb-sync-check-kb-meta",
    "${var.environment}-rds-init",
    "${var.environment}-orchestrator"
  ]
}
# create a custom metric filter to ensure all error-like logs are captured
resource "aws_cloudwatch_log_metric_filter" "lambda_errors" {
  for_each = toset(local.lambda_function_names)
  name     = "${each.key}-error-filter"
  pattern  = "?ERROR ?Error ?error ?Exception ?exception ?Fail ?fail"

  # Link the filterto the auto-created log groups for each function
  log_group_name = "/aws/lambda/${each.key}"

  metric_transformation {
    name          = "${each.key}-errors"
    namespace     = "Custom/Lambda"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  for_each = toset(local.lambda_function_names)

  alarm_name                = "${each.key}-error"
  comparison_operator       = "GreaterThanOrEqualToThreshold"
  evaluation_periods        = 1
  metric_name               = aws_cloudwatch_log_metric_filter.lambda_errors[each.key].metric_transformation[0].name
  namespace                 = aws_cloudwatch_log_metric_filter.lambda_errors[each.key].metric_transformation[0].namespace
  period                    = 60
  statistic                 = "Sum"
  threshold                 = 1
  alarm_description         = "Triggers if ${each.key} experiences 1 or more errors in a 1-minute window."
  insufficient_data_actions = []
  alarm_actions             = [local.sns_topic_arn]
  treat_missing_data        = "notBreaching"
}
