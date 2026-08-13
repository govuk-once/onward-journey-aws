# -----------------------------------------------------------------------------
# SECRETS MANAGER (To store the shared authentication token)
# -----------------------------------------------------------------------------
resource "aws_secretsmanager_secret" "auth_token" {
  name        = "${var.environment}/${var.authorizer_name}/auth-token"
  description = "Authentication token for ${var.authorizer_name} inbound webhook requests"
}

# (Note: The actual secret value is not defined in Terraform to prevent exposing it in state.
# It will be set manually in the AWS Console or via a secure CI/CD pipeline.)

# -----------------------------------------------------------------------------
# IAM ROLE & POLICIES
# -----------------------------------------------------------------------------
resource "aws_iam_role" "authorizer_role" {
  name = "${var.environment}-${var.authorizer_name}-authorizer-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_policy" "authorizer_policy" {
  name        = "${var.environment}-${var.authorizer_name}-authorizer-policy"
  description = "Permissions for Authorizer Lambda to read secrets and write logs"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "secretsmanager:GetSecretValue"
        Resource = aws_secretsmanager_secret.auth_token.arn
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "authorizer_attach" {
  role       = aws_iam_role.authorizer_role.name
  policy_arn = aws_iam_policy.authorizer_policy.arn
}

# -----------------------------------------------------------------------------
# ARCHIVE & LAMBDA FUNCTION (ARM64)
# -----------------------------------------------------------------------------
data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = var.lambda_source_dir
  output_path = "${path.module}/.terraform/archive/${var.authorizer_name}_authorizer.zip"
}

resource "aws_lambda_function" "authorizer" {
  function_name = "${var.environment}-${var.authorizer_name}-authorizer"
  role          = aws_iam_role.authorizer_role.arn
  handler       = "handler.lambda_handler"
  runtime       = "python3.12"
  architectures = ["arm64"]

  filename         = data.archive_file.lambda_zip.output_path
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256

  environment {
    variables = {
      SECRET_ARN   = aws_secretsmanager_secret.auth_token.arn
      PRINCIPAL_ID = var.authorizer_name
    }
  }
}

resource "aws_cloudwatch_log_group" "lambda_logs" {
  name              = "/aws/lambda/${aws_lambda_function.authorizer.function_name}"
  retention_in_days = var.log_retention_in_days
}
