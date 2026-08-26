output "invoke_arn" {
  description = "The invocation ARN of the signature authorizer Lambda"
  value       = module.authorizer_lambda.invoke_arn
}

output "function_name" {
  description = "The name of the signature authorizer Lambda function"
  value       = module.authorizer_lambda.function_name
}

output "function_arn" {
  description = "The ARN of the signature authorizer Lambda function"
  value       = module.authorizer_lambda.function_arn
}
