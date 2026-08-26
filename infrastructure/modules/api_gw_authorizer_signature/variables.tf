variable "environment" {
  description = "The deployment environment (e.g., dev, staging, prod)"
  type        = string
}

variable "authorizer_name" {
  description = "Identifier for the authorizer (e.g., crm-contentguru-wh)"
  type        = string
}

variable "lambda_source_dir" {
  description = "Path to the directory containing the Lambda function code"
  type        = string
}

variable "log_retention_in_days" {
  description = "Number of days to retain CloudWatch logs"
  type        = number
  default     = 14
}
