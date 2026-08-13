output "invoke_arn" {
  description = "The Invoke ARN of the Authorizer Lambda"
  value       = aws_lambda_function.authorizer.invoke_arn
}

output "function_name" {
  description = "The name of the Authorizer Lambda function"
  value       = aws_lambda_function.authorizer.function_name
}

output "secret_arn" {
  description = "The ARN of the Secrets Manager secret holding the auth token"
  value       = aws_secretsmanager_secret.auth_token.arn
}
