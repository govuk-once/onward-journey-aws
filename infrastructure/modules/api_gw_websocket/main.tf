resource "aws_apigatewayv2_api" "websocket" {
  name                       = "${var.environment}-${var.api_name}-ws-gw"
  protocol_type              = "WEBSOCKET"
  route_selection_expression = "$request.body.action"
  description                = var.api_description
}

resource "aws_apigatewayv2_integration" "router" {
  api_id           = aws_apigatewayv2_api.websocket.id
  integration_type = "AWS_PROXY"
  integration_uri  = var.target_lambda_arn
}

resource "aws_apigatewayv2_route" "connect" {
  api_id    = aws_apigatewayv2_api.websocket.id
  route_key = "$connect"
  target    = "integrations/${aws_apigatewayv2_integration.router.id}"
}

resource "aws_apigatewayv2_route" "disconnect" {
  api_id    = aws_apigatewayv2_api.websocket.id
  route_key = "$disconnect"
  target    = "integrations/${aws_apigatewayv2_integration.router.id}"
}

resource "aws_apigatewayv2_route" "default" {
  api_id    = aws_apigatewayv2_api.websocket.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.router.id}"
}

resource "aws_apigatewayv2_stage" "stage" {
  api_id      = aws_apigatewayv2_api.websocket.id
  name        = var.environment
  auto_deploy = true


  default_route_settings {
    # Disabled to prevent user chat payloads from being saved to CloudWatch.
    # Set to "ERROR" (or "INFO" - risks displaying PII) temporarily for perimeter debugging if API Gateway fails to invoke Lambda.
    logging_level            = "OFF"
    detailed_metrics_enabled = true
    throttling_burst_limit   = var.throttling_burst_limit
    throttling_rate_limit    = var.throttling_rate_limit
  }
}
