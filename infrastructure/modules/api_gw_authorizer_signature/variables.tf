variable "environment" {
  description = "Deployment environment name (e.g. dev, prod)"
  type        = string
}

variable "authorizer_name" {
  description = "Name identifier for the authorizer function"
  type        = string
}

variable "subnet_ids" {
  description = "List of private subnet IDs for VPC deployment"
  type        = list(string)
  default     = []
}

variable "security_group_ids" {
  description = "List of security group IDs for VPC deployment"
  type        = list(string)
  default     = []
}

variable "secretsmanager_endpoint_url" {
  description = "Custom endpoint URL for Secrets Manager VPC Endpoint"
  type        = string
  default     = ""
}
