locals {
  source_directory = var.source_dir != "" ? var.source_dir : var.function_name
}

data "archive_file" "zip" {
  type        = "zip"
  source_dir  = "${path.module}/../../../app/lambdas/${local.source_directory}"
  output_path = "${path.module}/../../../app/lambdas/${local.source_directory}.zip"
}

resource "aws_cloudwatch_log_group" "logs" {
  name              = "/aws/lambda/${var.environment}-${var.function_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "role" {
  name = "${var.environment}-${var.function_name}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

# Attach AWS-managed policy for VPC ENI provisioning when subnet_ids are supplied
resource "aws_iam_role_policy_attachment" "vpc_execution" {
  count      = length(var.subnet_ids) > 0 ? 1 : 0
  role       = aws_iam_role.role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_policy" "policy" {
  count       = var.policy_statements != null ? 1 : 0
  name        = "${var.environment}-${var.function_name}-policy"
  description = "Execution policy for ${var.function_name}"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Effect   = "Allow"
          Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
          Resource = "${aws_cloudwatch_log_group.logs.arn}:*"
        }
      ],
      var.policy_statements
    )
  })
}

resource "aws_iam_role_policy_attachment" "attach" {
  count      = var.policy_statements != null ? 1 : 0
  role       = aws_iam_role.role.name
  policy_arn = aws_iam_policy.policy[0].arn
}

# Wait 10 seconds for IAM policy propagation before Lambda attempts VPC ENI creation
resource "time_sleep" "wait_for_iam_propagation" {
  count           = length(var.subnet_ids) > 0 ? 1 : 0
  create_duration = "10s"

  depends_on = [
    aws_iam_role_policy_attachment.vpc_execution
  ]
}

resource "aws_lambda_function" "function" {
  filename                       = data.archive_file.zip.output_path
  source_code_hash               = data.archive_file.zip.output_base64sha256
  function_name                  = "${var.environment}-${var.function_name}"
  role                           = aws_iam_role.role.arn
  handler                        = var.handler
  runtime                        = "python3.12"
  architectures                  = ["arm64"]
  timeout                        = var.timeout
  reserved_concurrent_executions = var.reserved_concurrent_executions

  dynamic "vpc_config" {
    for_each = length(var.subnet_ids) > 0 ? [1] : []
    content {
      subnet_ids         = var.subnet_ids
      security_group_ids = var.security_group_ids
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.logs,
    aws_iam_role_policy_attachment.vpc_execution,
    time_sleep.wait_for_iam_propagation
  ]

  environment {
    variables = var.environment_variables
  }
}
