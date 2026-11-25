# -----------------------------------------------------------------------------
# LAMBDA: WEBHOOK TOKEN AUTHORIZER
# -----------------------------------------------------------------------------
module "crm_wh_token_authorizer" {
  source        = "../modules/lambda"
  environment   = var.environment
  function_name = "crm-wh-token-authorizer"
  description   = "Validates inbound CRM webhook URL query tokens"
  source_dir    = "api_gw_authorizer_token"

  subnet_ids         = local.private_subnet_ids
  security_group_ids = [aws_security_group.crm_wh_token_authorizer.id]

  environment_variables = {
    LOG_LEVEL            = "INFO"
    SECRET_ARN           = aws_secretsmanager_secret.dvla_storm_inbound_webhook_url_token.arn
    SECRETS_ENDPOINT_URL = "https://${aws_vpc_endpoint.endpoints["secrets"].dns_entry[0].dns_name}"
    TOKEN_QUERY_PARAM    = "token"
  }

  policy_statements = [
    {
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = aws_secretsmanager_secret.dvla_storm_inbound_webhook_url_token.arn
    }
  ]
}

# -----------------------------------------------------------------------------
# LAMBDA: CONTENT GURU STORM WEBHOOK VALIDATOR
# -----------------------------------------------------------------------------
module "crm_wh_validator_cgstorm" {
  source = "../modules/lambda"

  environment   = var.environment
  function_name = "crm-wh-validator-cgstorm"
  description   = "Verifies Content Guru Storm webhook signatures/challenges and dispatches events to router"
  source_dir    = "crm_wh_validator_cgstorm"

  subnet_ids         = local.private_subnet_ids
  security_group_ids = [aws_security_group.crm_wh_validator_cgstorm_sg.id]

  environment_variables = {
    LOG_LEVEL               = "INFO"
    DVLA_STORM_SECRET_ARN   = data.aws_secretsmanager_secret.dvla_storm_credentials.arn
    ROUTER_FUNCTION_NAME    = module.crm_wh_router.function_name
    SECRETS_ENDPOINT_URL    = "https://${aws_vpc_endpoint.endpoints["secrets"].dns_entry[0].dns_name}"
    AWS_ENDPOINT_URL_LAMBDA = "https://${aws_vpc_endpoint.endpoints["lambda"].dns_entry[0].dns_name}"
  }

  policy_statements = [
    {
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = data.aws_secretsmanager_secret.dvla_storm_credentials.arn
    },
    {
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = module.crm_wh_router.function_arn
    }
  ]
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

  subnet_ids         = local.private_subnet_ids
  security_group_ids = [aws_security_group.crm_wh_router_sg.id]

  environment_variables = {
    LOG_LEVEL           = "INFO"
    SESSIONS_TABLE_NAME = aws_dynamodb_table.sessions.name
    SESSIONS_GSI_NAME   = "crm_chat_id-index"
    WEBSOCKET_ENDPOINT  = replace(module.client_ws_gateway.wss_url, "wss://", "https://")
  }

  policy_statements = [
    {
      Effect = "Allow"
      Action = [
        "dynamodb:Query",
        "dynamodb:GetItem",
        "dynamodb:UpdateItem"
      ]
      Resource = [
        aws_dynamodb_table.sessions.arn,
        "${aws_dynamodb_table.sessions.arn}/index/crm_chat_id-index"
      ]
    },
    {
      Effect = "Allow"
      Action = [
        "execute-api:ManageConnections"
      ]
      Resource = [
        "${module.client_ws_gateway.execution_arn}/${var.environment}/POST/@connections/*"
      ]
    },
    {
      Effect   = "Allow"
      Action   = ["sqs:SendMessage"]
      Resource = [aws_sqs_queue.crm_wh_router_dlq.arn]
    }
  ]
}

# -----------------------------------------------------------------------------
# DEAD-LETTER QUEUE: WEBHOOK ROUTER
# -----------------------------------------------------------------------------
# Dead-lettter queue retains async webhook events if crm_wh_router fails after maximum retries
resource "aws_sqs_queue" "crm_wh_router_dlq" {
  name                      = "${var.environment}-crm-wh-router-dlq"
  message_retention_seconds = 604800 # 7 days

  tags = {
    Component = "crm-integration"
  }
}

# Directs failed asynchronous invocations from crm_wh_validator_cgstorm to the DLQ
resource "aws_lambda_function_event_invoke_config" "crm_wh_router_async_config" {
  function_name          = module.crm_wh_router.function_name
  maximum_retry_attempts = 2

  destination_config {
    on_failure {
      destination = aws_sqs_queue.crm_wh_router_dlq.arn
    }
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

  authorizer_lambda_invoke_arn    = module.crm_wh_token_authorizer.invoke_arn
  authorizer_lambda_function_name = module.crm_wh_token_authorizer.function_name

  integration_lambda_invoke_arn    = module.crm_wh_validator_cgstorm.invoke_arn
  integration_lambda_function_name = module.crm_wh_validator_cgstorm.function_name

  # WAF rate limits fall back to module defaults (100 req / 300 sec) in variables.tf
  # unless explicitly overridden here.
  # TODO(JOUR-298): Recalibrate WAF rate limit threshold prior to user testing (if required).
  # TODO(JOUR-299): Recalibrate WAF rate limit threshold prior to production release.
}
