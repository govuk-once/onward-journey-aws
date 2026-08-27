output "api_id" {
  description = "WebSocket API Gateway ID"
  value       = aws_apigatewayv2_api.websocket.id
}

output "execution_arn" {
  description = "Execution ARN used for Lambda permission scoping"
  value       = aws_apigatewayv2_api.websocket.execution_arn
}

output "wss_url" {
  description = "Target WSS URL for client connections"
  value       = aws_apigatewayv2_stage.stage.invoke_url
}
