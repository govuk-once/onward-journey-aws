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
