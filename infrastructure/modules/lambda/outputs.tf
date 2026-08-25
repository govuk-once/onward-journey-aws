output "function_name" {
  description = "Name of the Lambda function"
  value       = aws_lambda_function.function.function_name
}

output "function_arn" {
  description = "ARN of the Lambda function"
  value       = aws_lambda_function.function.arn
}

output "invoke_arn" {
  description = "Invoke ARN of the Lambda function"
  value       = aws_lambda_function.function.invoke_arn
}

output "role_name" {
  description = "IAM Role Name of the Lambda function"
  value       = aws_iam_role.role.name
}

output "role_arn" {
  description = "IAM Role ARN of the Lambda function"
  value       = aws_iam_role.role.arn
}
