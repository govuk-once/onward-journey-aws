output "rest_api_id" {
  description = "The ID of the REST API Gateway"
  value       = aws_api_gateway_rest_api.api.id
}

output "rest_api_arn" {
  description = "The ARN of the REST API Gateway"
  value       = aws_api_gateway_rest_api.api.arn
}

output "execution_arn" {
  description = "The execution ARN of the REST API Gateway"
  value       = aws_api_gateway_rest_api.api.execution_arn
}

output "stage_invoke_url" {
  description = "The base invocation URL for the stage"
  value       = aws_api_gateway_stage.stage.invoke_url
}

output "webhook_endpoint_url" {
  description = "Full URL endpoint for receiving webhooks"
  value       = "${aws_api_gateway_stage.stage.invoke_url}/${join("/", var.path_parts)}"
}
