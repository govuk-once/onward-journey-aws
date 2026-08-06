# Instantiates the generic REST API module specifically for Content Guru Storm webhooks
module "contentguru_webhook_gateway" {
  source = "../modules/api_gw_rest"

  environment     = var.environment
  api_name        = "contentguru-webhook"
  api_description = "Inbound REST API Gateway for Content Guru Storm webhooks"
  path_parts      = ["contentguru", "webhook"]

  authorizer_lambda_invoke_arn    = var.authorizer_lambda_invoke_arn
  authorizer_lambda_function_name = var.authorizer_lambda_function_name

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
