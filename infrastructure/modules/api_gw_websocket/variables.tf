variable "environment" {
  description = "Deployment environment (e.g., dev, staging, prod)"
  type        = string
}

variable "api_name" {
  description = "Name identifier for the WebSocket API"
  type        = string
}

variable "target_lambda_arn" {
  description = "Invoke ARN of the router Lambda handling WebSocket routes"
  type        = string
}

variable "authorizer_lambda_arn" {
  description = "Invoke ARN of the Lambda authoriser for the $connect route"
  type        = string
  default     = null
}

variable "api_description" {
  description = "Description of the WebSocket API Gateway instance"
  type        = string
  default     = "Inbound WebSocket API Gateway for client connections"
}

variable "throttling_burst_limit" {
  description = "Maximum peak request rate allowed by API Gateway"
  type        = number
  default     = 200
}

variable "throttling_rate_limit" {
  description = "Maximum steady-state request rate allowed by API Gateway"
  type        = number
  default     = 100
}

variable "enable_connect_authorizer" {
  description = "Set to true to provision a REQUEST authoriser on the $connect route"
  type        = bool
  default     = false
}
