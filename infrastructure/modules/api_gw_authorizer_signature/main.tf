# -----------------------------------------------------------------------------
# SECRETS MANAGER (To store the shared HMAC signing secret)
# -----------------------------------------------------------------------------
resource "aws_secretsmanager_secret" "signing_secret" {
  name        = "${var.environment}/${var.authorizer_name}/signing-secret"
  description = "HMAC-SHA256 signing secret for ${var.authorizer_name} inbound webhook requests"
}

# (Note: The actual secret value is not defined in Terraform to prevent exposing it in state.
# It will be set manually in the AWS Console or via a secure CI/CD pipeline.)

# -----------------------------------------------------------------------------
# AUTHORIZER LAMBDA MODULE
# -----------------------------------------------------------------------------
module "authorizer_lambda" {
  source        = "../lambda"
  environment   = var.environment
  function_name = "${var.authorizer_name}-authorizer"
  source_dir    = "api_gw_authorizer_signature"

  subnet_ids         = var.subnet_ids
  security_group_ids = var.security_group_ids

  environment_variables = {
    SECRET_ARN                   = aws_secretsmanager_secret.signing_secret.arn
    PRINCIPAL_ID                 = var.authorizer_name
    SECRETS_MANAGER_ENDPOINT_URL = var.secretsmanager_endpoint_url
  }

  policy_statements = [
    {
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = aws_secretsmanager_secret.signing_secret.arn
    }
  ]
}
