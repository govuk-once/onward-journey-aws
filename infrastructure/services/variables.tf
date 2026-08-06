variable "environment" {
  type        = string
  description = "The name of the environment for this instance of the infrastructure, e.g. 'development', 'staging', or <your initials>"
}

variable "aws_account_id" {
  type        = string
  description = "The AWS account ID where this is being deployed"

  validation {
    condition     = can(regex("^\\d{12}$", var.aws_account_id))
    error_message = "The AWS account ID should be a 12-digit number"
  }
}

variable "aws_region" {
  type        = string
  description = "The AWS region to deploy resources into (e.g. eu-west-2)."
  default     = "eu-west-2"
}

variable "authorizer_lambda_invoke_arn" {
  description = "The invoke ARN of the custom Authorizer Lambda function"
  type        = string
  default     = ""
}

variable "authorizer_lambda_function_name" {
  description = "The function name of the custom Authorizer Lambda"
  type        = string
  default     = ""
}

variable "processor_lambda_invoke_arn" {
  description = "The invoke ARN of the backend Processor Lambda function"
  type        = string
  default     = ""
}

variable "processor_lambda_function_name" {
  description = "The function name of the backend Processor Lambda"
  type        = string
  default     = ""
}
