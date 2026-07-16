## COGNITO IDENTITY POOL
# Provides temporary, scoped AWS credentials to anonymous frontend users
# so they can invoke the Orchestrator Lambda URL (which uses AWS_IAM auth).

resource "aws_cognito_identity_pool" "frontend_anon" {
  identity_pool_name               = "${var.environment}-onward-journey-anon"
  allow_unauthenticated_identities = true
  # No authenticated providers – this pool is intentionally anonymous.
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
