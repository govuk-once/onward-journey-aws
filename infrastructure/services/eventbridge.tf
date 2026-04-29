
# -----------------------------------------------------------------------------
# 1. IAM Role for EventBridge to Invoke Step Functions
# -----------------------------------------------------------------------------
resource "aws_iam_role" "eventbridge_sfn_invoke_role" {
  name = "${var.environment}-eventbridge-sfn-invoke-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "events.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_policy" "eventbridge_sfn_invoke_policy" {
  name        = "${var.environment}-eventbridge-sfn-invoke-policy"
  description = "Allows EventBridge to trigger the KB Sync State Machine"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "states:StartExecution"
        ]
        Resource = [
          aws_sfn_state_machine.kb_sync_machine.arn
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "eventbridge_sfn_invoke_attach" {
  role       = aws_iam_role.eventbridge_sfn_invoke_role.name
  policy_arn = aws_iam_policy.eventbridge_sfn_invoke_policy.arn
}

# -----------------------------------------------------------------------------
# 2. Dynamic EventBridge Rules (One per enabled pipeline)
# -----------------------------------------------------------------------------

# Create an EventBridge rule for each enabled pipeline from the YAML
resource "aws_cloudwatch_event_rule" "kb_sync_schedule" {
  for_each = local.active_pipelines

  name                = "${var.environment}-kb-sync-${each.key}"
  description         = "Triggers the KB Sync Step Function for ${each.value.kb_identifier}"
  schedule_expression = each.value.schedule_expression
}

# Link the rules to the single Step Function
resource "aws_cloudwatch_event_target" "kb_sync_target" {
  for_each = local.active_pipelines

  rule      = aws_cloudwatch_event_rule.kb_sync_schedule[each.key].name
  target_id = "TriggerStepFunction-${each.key}"
  arn       = aws_sfn_state_machine.kb_sync_machine.arn
  role_arn  = aws_iam_role.eventbridge_sfn_invoke_role.arn

  # Pass the YAML variables into the Step Function as JSON input
  input = jsonencode({
    "kb_identifier" : each.value.kb_identifier,
    "platform" : each.value.platform,
    "sync_type" : "scheduled"
  })
}
