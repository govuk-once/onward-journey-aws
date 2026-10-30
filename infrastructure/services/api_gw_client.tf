# -----------------------------------------------------------------------------
# WEBSOCKET AUTHORISER LAMBDA
# -----------------------------------------------------------------------------
module "client_ws_authorizer_lambda" {
  source        = "../modules/lambda"
  environment   = var.environment
  function_name = "client-ws-authorizer"
  source_dir    = "api_gw_authorizer_token"

  subnet_ids         = local.private_subnet_ids
  security_group_ids = [aws_security_group.crm_contentguru_wh_authorizer.id]

  environment_variables = {
    ENVIRONMENT          = var.environment
    SECRET_ARN           = aws_secretsmanager_secret.ws_auth_tokens.arn
    SECRETS_ENDPOINT_URL = "https://${aws_vpc_endpoint.endpoints["secrets"].dns_entry[0].dns_name}"
  }

  policy_statements = [
    {
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = aws_secretsmanager_secret.ws_auth_tokens.arn
    }
  ]
}

resource "aws_lambda_permission" "allow_websocket_invoke_authorizer" {
  statement_id  = "AllowWebSocketGatewayInvokeAuthorizer"
  action        = "lambda:InvokeFunction"
  function_name = module.client_ws_authorizer_lambda.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${module.client_ws_gateway.execution_arn}/*/*"
}

# -----------------------------------------------------------------------------
# WEBSOCKET API GATEWAY
# -----------------------------------------------------------------------------
module "client_ws_gateway" {
  source                = "../modules/api_gw_websocket"
  environment           = var.environment
  api_name              = "client"
  target_lambda_arn     = module.client_ws_router_lambda.invoke_arn
  authorizer_lambda_arn = module.client_ws_authorizer_lambda.invoke_arn
  api_description       = "Inbound WebSocket API Gateway for client browser connections"
}

# -----------------------------------------------------------------------------
# WEBSOCKET ROUTER LAMBDA
# -----------------------------------------------------------------------------
module "client_ws_router_lambda" {
  source        = "../modules/lambda"
  environment   = var.environment
  function_name = "client-ws-router"
  source_dir    = "client_ws_router"
  timeout       = 29

  environment_variables = {
    ENVIRONMENT                = var.environment
    AGENT_RUNTIME_ARN          = aws_bedrockagentcore_agent_runtime.orchestrator_runtime.agent_runtime_arn
    SESSIONS_TABLE_NAME        = aws_dynamodb_table.sessions.name
    CRM_TOOL_LAMBDA_ARN        = aws_lambda_function.crm_tool.arn
    AGENT_RUNTIME_ENDPOINT_URL = "https://${aws_vpc_endpoint.endpoints["bedrock_agentcore"].dns_entry[0].dns_name}"
  }

  policy_statements = [
    {
      Effect   = "Allow"
      Action   = ["bedrock-agentcore:InvokeAgentRuntime"]
      Resource = "${aws_bedrockagentcore_agent_runtime.orchestrator_runtime.agent_runtime_arn}*"
    },
    {
      Effect = "Allow"
      Action = [
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:UpdateItem"
      ]
      Resource = aws_dynamodb_table.sessions.arn
    },
    {
      Effect   = "Allow"
      Action   = ["lambda:InvokeFunction"]
      Resource = aws_lambda_function.crm_tool.arn
    }
  ]
}

resource "aws_lambda_permission" "allow_websocket_invoke" {
  statement_id  = "AllowWebSocketGatewayInvokeClient"
  action        = "lambda:InvokeFunction"
  function_name = module.client_ws_router_lambda.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${module.client_ws_gateway.execution_arn}/*/*"
}
