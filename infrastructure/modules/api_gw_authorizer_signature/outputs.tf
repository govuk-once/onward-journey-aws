output "invoke_arn" {
  description = "The Invoke ARN of the Signature Authorizer Lambda"
  value       = aws_lambda_function.authorizer.invoke_arn
}

output "function_name" {
  description = "The name of the Signature Authorizer Lambda function"
  value       = aws_lambda_function.authorizer.function_name
}

output "signing_secret_arn" {
  description = "The ARN of the Secrets Manager secret holding the HMAC signing secret"
  value       = aws_secretsmanager_secret.signing_secret.arn
}
