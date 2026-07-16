## COGNITO IDENTITY POOL
# Provides temporary, scoped AWS credentials to anonymous frontend users
# so they can invoke the Orchestrator Lambda URL (which uses AWS_IAM auth).

resource "aws_cognito_identity_pool" "frontend_anon" {
  identity_pool_name               = "${var.environment}-onward-journey-anon"
  allow_unauthenticated_identities = true
  # No authenticated providers – this pool is intentionally anonymous.
}

## UNAUTHENTICATED IDENTITY ROLE
# The principal here is "cognito-identity.amazonaws.com", scoped to this pool
# and to the unauthenticated context only.

data "aws_iam_policy_document" "cognito_anon_assume" {
  statement {
    sid     = "CognitoUnauthAssumeRole"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = ["cognito-identity.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "cognito-identity.amazonaws.com:aud"
      values   = [aws_cognito_identity_pool.frontend_anon.id]
    }

    condition {
      test     = "ForAnyValue:StringLike"
      variable = "cognito-identity.amazonaws.com:amr"
      values   = ["unauthenticated"]
    }
  }
}

resource "aws_iam_role" "cognito_anon_role" {
  name               = "${var.environment}-cognito-anon-role"
  assume_role_policy = data.aws_iam_policy_document.cognito_anon_assume.json
}

## INVOKE PERMISSION
# Grants only lambda:InvokeFunctionUrl on the specific Orchestrator Lambda.

resource "aws_iam_policy" "cognito_anon_invoke" {
  name        = "${var.environment}-cognito-anon-invoke"
  description = "Allows Cognito unauthenticated identities to invoke the Orchestrator Lambda URL."

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "InvokeOrchestratorUrl"
        Effect   = "Allow"
        Action   = "lambda:InvokeFunctionUrl"
        Resource = aws_lambda_function.orchestrator.arn
        Condition = {
          StringEquals = {
            "lambda:FunctionUrlAuthType" = "AWS_IAM"
          }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "cognito_anon_invoke" {
  role       = aws_iam_role.cognito_anon_role.name
  policy_arn = aws_iam_policy.cognito_anon_invoke.arn
}

## ROLE ATTACHMENT TO POOL

resource "aws_cognito_identity_pool_roles_attachment" "frontend_anon" {
  identity_pool_id = aws_cognito_identity_pool.frontend_anon.id

  roles = {
    unauthenticated = aws_iam_role.cognito_anon_role.arn
  }
}

output "cognito_identity_pool_id" {
  description = "The ID of the Cognito Identity Pool used for anonymous frontend auth."
  value       = aws_cognito_identity_pool.frontend_anon.id
}
