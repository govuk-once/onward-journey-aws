variable "environment" {
  description = "The deployment environment (e.g., dev, staging, prod)"
  type        = string
}

variable "api_name" {
  description = "Generic identifier for the API Gateway (e.g., contentguru-webhook)"
  type        = string
}

variable "api_description" {
  description = "Description of the API Gateway instance"
  type        = string
  default     = "REST API Gateway managed by Terraform"
}

variable "path_parts" {
  description = "Ordered list of URL path segments (e.g., [\"contentguru\", \"webhook\"] for /contentguru/webhook)"
  type        = list(string)
}

variable "authorizer_lambda_invoke_arn" {
  description = "The invoke ARN of the Lambda function handling custom request authorisation"
  type        = string
}

variable "authorizer_lambda_function_name" {
  description = "The function name of the Authorizer Lambda (needed for invocation permissions)"
  type        = string
}

variable "processor_lambda_invoke_arn" {
  description = "The invoke ARN of the backend Lambda function processing validated webhook payloads"
  type        = string
}

variable "processor_lambda_function_name" {
  description = "The function name of the Processor Lambda (needed for invocation permissions)"
  type        = string
}

variable "waf_rate_limit" {
  description = "Maximum requests allowed from a single IP within the evaluation window"
  type        = number
  default     = 100
}

variable "waf_evaluation_window_sec" {
  description = "Evaluation window in seconds for the rate-limiting rule (60, 120, 300, or 600)"
  type        = number
  default     = 300
}
