
# -----------------------------------------------------------------------------
# 1. IAM Role for Step Functions Execution
# -----------------------------------------------------------------------------
resource "aws_iam_role" "sfn_kb_sync_role" {
  name = "${var.environment}-sfn-kb-sync-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "states.amazonaws.com"
        }
      }
    ]
  })
}

# -----------------------------------------------------------------------------
# 2. IAM Policy to allow invoking ETL Lambdas & CloudWatch logging
# -----------------------------------------------------------------------------
resource "aws_iam_policy" "sfn_kb_sync_policy" {
  name        = "${var.environment}-sfn-kb-sync-policy"
  description = "Permissions for KB Sync Step Function to invoke task lambdas"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "lambda:InvokeFunction"
        ]
        Resource = [
          aws_lambda_function.kb_sync_check_meta.arn,
          aws_lambda_function.kb_sync_fetch_articles.arn,
          aws_lambda_function.kb_sync_upsert.arn
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery",
          "logs:GetLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries",
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "sfn_kb_sync_attach" {
  role       = aws_iam_role.sfn_kb_sync_role.name
  policy_arn = aws_iam_policy.sfn_kb_sync_policy.arn
}

# -----------------------------------------------------------------------------
# 3. CloudWatch Log Group for the State Machine
# -----------------------------------------------------------------------------
resource "aws_cloudwatch_log_group" "sfn_kb_sync_logs" {
  name              = "/aws/vendedlogs/states/${var.environment}-kb-sync-machine"
  retention_in_days = 14
}

# -----------------------------------------------------------------------------
# 4. Step Function State Machine
# -----------------------------------------------------------------------------
resource "aws_sfn_state_machine" "kb_sync_machine" {
  name     = "${var.environment}-kb-sync-machine"
  role_arn = aws_iam_role.sfn_kb_sync_role.arn

  definition = templatefile("${path.module}/kb_sync_workflow.asl.json", {
    check_meta_lambda_arn     = aws_lambda_function.kb_sync_check_meta.arn
    fetch_articles_lambda_arn = aws_lambda_function.kb_sync_fetch_articles.arn
    upsert_lambda_arn         = aws_lambda_function.kb_sync_upsert.arn
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn_kb_sync_logs.arn}:*"
    include_execution_data = true
    level                  = "ERROR"
  }

  depends_on = [
    aws_iam_role_policy_attachment.sfn_kb_sync_attach
  ]
}
