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

resource "aws_lambda_function" "function" {
  filename         = data.archive_file.zip.output_path
  source_code_hash = data.archive_file.zip.output_base64sha256
  function_name    = "${var.environment}-${var.function_name}"
  role             = aws_iam_role.role.arn
  handler          = var.handler
  runtime          = "python3.12"
  architectures    = ["arm64"]
  timeout          = var.timeout

  depends_on = [
    aws_cloudwatch_log_group.logs
  ]

  environment {
    variables = var.environment_variables
  }
}
