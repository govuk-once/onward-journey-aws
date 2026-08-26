# -----------------------------------------------------------------------------
# AUTHORISER LAMBDA MODULES
# -----------------------------------------------------------------------------
module "crm_contentguru_wh_authorizer" {
  source                = "../modules/api_gw_authorizer_signature"
  environment           = var.environment
  authorizer_name       = "crm_contentguru_wh"
  lambda_source_dir     = "${path.module}/../../app/lambdas/api_gw_authorizer_signature"
  log_retention_in_days = 14
}

# -----------------------------------------------------------------------------
# API GATEWAY REST MODULES
# -----------------------------------------------------------------------------
module "crm_contentguru_wh_gateway" {
  source = "../modules/api_gw_rest"

  environment     = var.environment
  api_name        = "crm_contentguru_wh"
  api_description = "Inbound REST API Gateway for Content Guru Storm webhooks"
  path_parts      = ["contentguru", "webhook"]

  authorizer_lambda_invoke_arn    = module.crm_contentguru_wh_authorizer.invoke_arn
  authorizer_lambda_function_name = module.crm_contentguru_wh_authorizer.function_name

  # TODO(JOUR-346): Replace empty string with the real processor Lambda function name and invoke ARN once downstream compute is provisioned
  processor_lambda_function_name = ""
  processor_lambda_invoke_arn    = ""

  # WAF rate limits fall back to module defaults (100 req / 300 sec) in variables.tf
  # unless explicitly overridden here.
  # TODO(JOUR-298): Recalibrate WAF rate limit threshold prior to user testing.
  # TODO(JOUR-299): Recalibrate WAF rate limit threshold prior to production release.
}

output "crm_contentguru_wh_url" {
  description = "The target URL to register in Content Guru Storm Integrate (CRM management)"
  value       = module.crm_contentguru_wh_gateway.webhook_endpoint_url
}
