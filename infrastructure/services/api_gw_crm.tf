# -----------------------------------------------------------------------------
# AUTHORISER LAMBDA MODULES
# -----------------------------------------------------------------------------
module "contentguru_webhook_authorizer" {
  source                = "../modules/api_gw_authorizer_signature"
  environment           = var.environment
  authorizer_name       = "contentguru-webhook"
  lambda_source_dir     = "${path.module}/../../app/lambdas/api_gw_authorizer_signature"
  log_retention_in_days = 14
}

# -----------------------------------------------------------------------------
# API GATEWAY REST MODULES
# -----------------------------------------------------------------------------
module "contentguru_webhook_gateway" {
  source = "../modules/api_gw_rest"

  environment     = var.environment
  api_name        = "contentguru-webhook"
  api_description = "Inbound REST API Gateway for Content Guru Storm webhooks"
  path_parts      = ["contentguru", "webhook"]

  authorizer_lambda_invoke_arn    = module.contentguru_webhook_authorizer.invoke_arn
  authorizer_lambda_function_name = module.contentguru_webhook_authorizer.function_name

  processor_lambda_invoke_arn    = var.processor_lambda_invoke_arn
  processor_lambda_function_name = var.processor_lambda_function_name

  # WAF rate limits fall back to module defaults (100 req / 300 sec) in variables.tf
  # unless explicitly overridden here.
  # TODO(JOUR-298): Recalibrate WAF rate limit threshold prior to user testing.
  # TODO(JOUR-299): Recalibrate WAF rate limit threshold prior to production release.
}

output "contentguru_webhook_url" {
  description = "The target URL to register in Content Guru Storm Integrate (CRM management)"
  value       = module.contentguru_webhook_gateway.webhook_endpoint_url
}
