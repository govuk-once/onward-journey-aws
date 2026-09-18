# -----------------------------------------------------------------------------
# LAMBDA: WEBHOOK AUTHORIZER
# -----------------------------------------------------------------------------
module "crm_contentguru_wh_authorizer" {
  source          = "../modules/api_gw_authorizer_signature"
  environment     = var.environment
  authorizer_name = "crm-contentguru-wh"

  subnet_ids                  = local.private_subnet_ids
  security_group_ids          = [aws_security_group.crm_contentguru_wh_authorizer.id]
  secretsmanager_endpoint_url = "https://${aws_vpc_endpoint.secrets.dns_entry[0].dns_name}"
}

# -----------------------------------------------------------------------------
# LAMBDA: WEBHOOK ROUTER
# -----------------------------------------------------------------------------
module "crm_wh_router" {
  source = "../modules/lambda"

  environment   = var.environment
  function_name = "crm-wh-router"
  description   = "Ingests webhooks from external CRM platforms and routes messages to active WebSocket sessions"
  source_dir    = "crm_wh_router"

  # Compute Protection: Prevent spikes from exhausting shared account concurrency
  reserved_concurrent_executions = 20

  environment_variables = {
    LOG_LEVEL = "INFO"
  }
}

# -----------------------------------------------------------------------------
# REST API GATEWAY
# -----------------------------------------------------------------------------
module "crm_contentguru_wh_gateway" {
  source = "../modules/api_gw_rest"

  environment     = var.environment
  api_name        = "crm-contentguru-wh"
  api_description = "Inbound REST API Gateway for Content Guru Storm webhooks"
  path_parts      = ["contentguru", "webhook"]

  authorizer_lambda_invoke_arn    = module.crm_contentguru_wh_authorizer.invoke_arn
  authorizer_lambda_function_name = module.crm_contentguru_wh_authorizer.function_name

  crm_wh_router_invoke_arn    = module.crm_wh_router.invoke_arn
  crm_wh_router_function_name = module.crm_wh_router.function_name

  # WAF rate limits fall back to module defaults (100 req / 300 sec) in variables.tf
  # unless explicitly overridden here.
  # TODO(JOUR-298): Recalibrate WAF rate limit threshold prior to user testing.
  # TODO(JOUR-299): Recalibrate WAF rate limit threshold prior to production release.
}

output "crm_contentguru_wh_url" {
  description = "The target URL to register in Content Guru Storm Integrate (CRM management)"
  value       = module.crm_contentguru_wh_gateway.webhook_endpoint_url
}
