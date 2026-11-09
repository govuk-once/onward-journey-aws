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

output "log_group_name" {
  description = "The name of the CloudWatch log group for the signature authorizer Lambda"
  value       = module.authorizer_lambda.log_group_name
}
