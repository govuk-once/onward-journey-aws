## AGENTCORE IDENTIFIERS
# These IDs are required for the Orchestrator's environment variables.

output "agent_chat_context_id" {
  description = "The unique identifier for AgentCore Memory."
  value       = aws_bedrockagentcore_memory.agent_chat_context.id
}

output "tool_interface_id" {
  description = "The unique identifier for the AgentCore Gateway."
  value       = aws_bedrockagentcore_gateway.tool_interface.gateway_id
}

## AGENTCORE RUNTIME

output "agentcore_runtime_id" {
  description = "The ID of the new AgentCore runtime for console testing"
  value       = aws_bedrockagentcore_agent_runtime.orchestrator_runtime.agent_runtime_id
}

## WEBSOCKET GATEWAY URL
output "client_websocket_url" {
  description = "The WSS URL for client browser connections"
  value       = module.client_ws_gateway.wss_url
}

## RESOURCE ARNs
# Required for IAM policies and cross-account references.

output "agent_chat_context_arn" {
  description = "The ARN of the managed chat history store."
  value       = aws_bedrockagentcore_memory.agent_chat_context.arn
}

output "tool_interface_arn" {
  description = "The ARN of the tool gateway for MCP connectivity."
  value       = aws_bedrockagentcore_gateway.tool_interface.gateway_arn
}

## NETWORKING
output "orchestrator_sg_id" {
  description = "The ID of the security group for the orchestration layer."
  value       = aws_security_group.orchestrator.id
}

## DYNAMODB STATE MANAGEMENT DATABASE
output "dynamodb_sessions_table_name" {
  description = "Name of the DynamoDB sessions table"
  value       = aws_dynamodb_table.sessions.name
}

output "dynamodb_sessions_table_arn" {
  description = "ARN of the DynamoDB sessions table"
  value       = aws_dynamodb_table.sessions.arn
}

output "dynamodb_sessions_crm_chat_id_index" {
  description = "Name of the GSI used to look up sessions by Storm CRM chat ID"
  value       = "crm_chat_id-index"
}
