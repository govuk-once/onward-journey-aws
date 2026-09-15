variable "environment" {
  description = "Deployment environment name (e.g. dev, prod)"
  type        = string
}

variable "authorizer_name" {
  description = "Name identifier for the authorizer function"
  type        = string
}
