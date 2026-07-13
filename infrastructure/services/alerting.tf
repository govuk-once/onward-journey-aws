# DEPENDENCY: slack-alerting shared infrastructure (infrastructure/slack-alerting) must be applied before this workspace
variable "sns_topic_arn" {
  type        = string
  description = "The ARN of the SNS topic for AWS error alerting via Slack"
}

locals {
  sns_topic_arn = var.sns_topic_arn
  main_log_groups = [
    aws_cloudwatch_log_group.rds_seeder.name,
    aws_cloudwatch_log_group.crm_tool.name,
    aws_cloudwatch_log_group.rds_init.name,
    aws_cloudwatch_log_group.rds_tool.name,
    aws_cloudwatch_log_group.orchestrator.name
  ]

  kb_sync_functions = [
    "${var.environment}-kb-sync-fetch-articles",
    "${var.environment}-kb-sync-upsert",
    "${var.environment}-kb-sync-check-sync-meta",
    "${var.environment}-kb-sync-update-sync-meta",
    "${var.environment}-kb-sync-check-kb-meta",
  ]
  sync_machine = "${var.environment}-kb-sync-machine"

}
# ============== Main Log Groups ======================================
resource "aws_cloudwatch_log_metric_filter" "logged_errors" {
  for_each = toset(local.main_log_groups)
  name     = "${regex("[^/]+$", each.key)}-error-filter"
  pattern  = "?ERROR ?Error ?error ?Exception ?exception ?Fail ?fail"

  # Link the filter to the auto-created log groups for each function
  log_group_name = each.key

  # use the filter to increment a metric
  metric_transformation {
    name          = "${regex("[^/]+$", each.key)}-errors"
    namespace     = "Custom/Lambda"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "logged_errors" {
  for_each = toset(local.main_log_groups)

  alarm_name                = "${regex("[^/]+$", each.key)}-error"
  comparison_operator       = "GreaterThanOrEqualToThreshold"
  evaluation_periods        = 1
  metric_name               = aws_cloudwatch_log_metric_filter.logged_errors[each.key].metric_transformation[0].name
  namespace                 = aws_cloudwatch_log_metric_filter.logged_errors[each.key].metric_transformation[0].namespace
  period                    = 60
  statistic                 = "Sum"
  threshold                 = 1
  alarm_description         = "Triggers if an error message is detected in Cloudwatch logs. An error was detected in the logs for ${each.key}"
  insufficient_data_actions = []
  alarm_actions             = [local.sns_topic_arn]
  treat_missing_data        = "notBreaching"
}

# ============== KB-sync Lambdas ======================================

resource "aws_cloudwatch_log_metric_filter" "kb_sync_lambda_errors" {
  for_each = toset(local.kb_sync_functions)
  name     = "${each.key}-error-filter"
  pattern  = "?ERROR ?Error ?error ?Exception ?exception ?Fail ?fail"

  # Link the filter to the auto-created log groups for each function
  log_group_name = "/aws/lambda/${each.key}"

  # use the filter to increment a metric
  metric_transformation {
    name          = "${var.environment}-kb-sync-lambda-errors"
    namespace     = "Custom/Lambda"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "kb_sync_lambda_errors" {

  alarm_name                = "${var.environment}-kb-sync-lambdas-logged-error"
  comparison_operator       = "GreaterThanOrEqualToThreshold"
  evaluation_periods        = 1
  metric_name               = "${var.environment}-kb-sync-lambda-errors"
  namespace                 = "Custom/Lambda"
  period                    = 60
  statistic                 = "Sum"
  threshold                 = 1
  alarm_description         = "Triggers if an error message is logged in any of the KB sync lambdas, even if the step function succeeds. Check individual kb-sync function logs"
  insufficient_data_actions = []
  alarm_actions             = [local.sns_topic_arn]
  treat_missing_data        = "notBreaching"
}

# ============== Step Function ======================================

resource "aws_cloudwatch_log_metric_filter" "step_function_errors" {
  name    = "${var.environment}-step-function-error-filter"
  pattern = "?ERROR ?Error ?error ?Exception ?exception ?Fail ?fail"

  # Link the filter to the auto-created log groups for each function
  log_group_name = "/aws/vendedlogs/states/${local.sync_machine}"

  # use the filter to increment a metric
  metric_transformation {
    name          = "${var.environment}-step-function-errors"
    namespace     = "Custom/Lambda"
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_metric_alarm" "step_function_errors" {

  alarm_name                = "${var.environment}-step-function-error"
  comparison_operator       = "GreaterThanOrEqualToThreshold"
  evaluation_periods        = 1
  metric_name               = "${var.environment}-step-function-errors"
  namespace                 = "Custom/Lambda"
  period                    = 60
  statistic                 = "Sum"
  threshold                 = 1
  alarm_description         = "Triggers if the step function logs an error or fails to complete a KB sync"
  insufficient_data_actions = []
  alarm_actions             = [local.sns_topic_arn]
  treat_missing_data        = "notBreaching"
}
