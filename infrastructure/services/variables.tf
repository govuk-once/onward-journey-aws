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

# TODO: Uncomment the following variable when the credentials are required by the crm_tool in future DVLA Storm integration work.
# Currently commented out as it will flag as a tflint error whilst not being used in any resources or outputs.
# variable "dvla_storm_secret_name" {
#   type        = string
#   description = "Secrets Manager secret name containing DVLA Content Guru Storm API credentials"
#   default     = "shared/crm-creds/dvla-storm-dev"
# }
