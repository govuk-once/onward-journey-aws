# -----------------------------------------------------------------------------
# WEBSOCKET API GATEWAY
# -----------------------------------------------------------------------------
module "client_websocket_gateway" {
  source            = "../modules/api_gw_websocket"
  environment       = var.environment
  api_name          = "client"
  target_lambda_arn = module.ws_client_router_lambda.invoke_arn
}

# -----------------------------------------------------------------------------
# WEBSOCKET ROUTER LAMBDA
# -----------------------------------------------------------------------------
module "ws_client_router_lambda" {
  source        = "../modules/lambda"
  environment   = var.environment
  function_name = "ws_client_router"
  timeout       = 29

  environment_variables = {
    ENVIRONMENT       = var.environment
    AGENT_RUNTIME_ARN = aws_bedrockagentcore_agent_runtime.orchestrator_runtime.agent_runtime_arn
  }

  policy_statements = [
    {
      Effect   = "Allow"
      Action   = ["execute-api:ManageConnections"]
      Resource = "${module.client_websocket_gateway.execution_arn}/*"
    },
    {
      Effect   = "Allow"
      Action   = ["bedrock-agentcore:InvokeAgentRuntime"]
      Resource = "${aws_bedrockagentcore_agent_runtime.orchestrator_runtime.agent_runtime_arn}*"
    }
  ]
}

# -----------------------------------------------------------------------------
# LAMBDA INVOCATION PERMISSIONS
# -----------------------------------------------------------------------------
resource "aws_lambda_permission" "allow_websocket_invoke" {
  statement_id  = "AllowWebSocketGatewayInvokeClient"
  action        = "lambda:InvokeFunction"
  function_name = module.ws_client_router_lambda.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${module.client_websocket_gateway.execution_arn}/*/*"
}
