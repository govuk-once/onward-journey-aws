variable "environment" {
  description = "Deployment environment (e.g., dev, staging, prod)"
  type        = string
}

variable "function_name" {
  description = "Name identifier of the Lambda function (matches source directory name under app/lambdas/)"
  type        = string
}

variable "handler" {
  description = "Lambda function entrypoint"
  type        = string
  default     = "handler.lambda_handler"
}

variable "timeout" {
  description = "Function execution timeout in seconds"
  type        = number
  default     = 29
}

variable "log_retention_days" {
  description = "CloudWatch log group retention period in days"
  type        = number
  default     = 14
}

variable "environment_variables" {
  description = "Map of environment variables to pass to the function"
  type        = map(string)
  default     = {}
}

variable "policy_statements" {
  description = "List of IAM policy statement blocks to append to the execution policy"
  type        = list(any)
  default     = null
}
