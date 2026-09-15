/**
 * PURPOSE: Private Connectivity (VPC Endpoints).
 * These endpoints allow resources in private subnets to securely
 * communicate with AWS services without traversing the public internet.
 */

# 1. Bedrock Endpoint - Required for LLM inference and embeddings
resource "aws_vpc_endpoint" "bedrock" {
  vpc_id            = local.vpc_id
  service_name      = "com.amazonaws.${var.aws_region}.bedrock-runtime"
  vpc_endpoint_type = "Interface"

  # Deploying into the private application subnets
  subnet_ids          = local.private_subnet_ids
  security_group_ids  = [aws_security_group.vpc_endpoints.id, aws_security_group.bedrock.id]
  private_dns_enabled = false

  tags = { Name = "${var.environment}-bedrock-endpoint" }
}

# 2. Secrets Manager Endpoint - Required to retrieve the DB password
resource "aws_vpc_endpoint" "secrets" {
  vpc_id            = local.vpc_id
  service_name      = "com.amazonaws.${var.aws_region}.secretsmanager"
  vpc_endpoint_type = "Interface"

  subnet_ids          = local.private_subnet_ids
  security_group_ids  = [aws_security_group.vpc_endpoints.id, aws_security_group.secrets_manager.id]
  private_dns_enabled = false

  tags = { Name = "${var.environment}-secrets-endpoint" }
}

# 3. Lambda Endpoint - Required for rds_seeder to invoke crm_tool from within the VPC
resource "aws_vpc_endpoint" "lambda" {
  vpc_id            = local.vpc_id
  service_name      = "com.amazonaws.${var.aws_region}.lambda"
  vpc_endpoint_type = "Interface"

  subnet_ids          = local.private_subnet_ids
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = false

  tags = { Name = "${var.environment}-lambda-endpoint" }
}

# 3. Bedrock AgentCore Endpoint - REQUIRED for Memory/Checkpointer & Gateway
resource "aws_vpc_endpoint" "bedrock_agentcore" {
  vpc_id             = local.vpc_id
  service_name       = "com.amazonaws.${var.aws_region}.bedrock-agentcore"
  vpc_endpoint_type  = "Interface"
  subnet_ids         = local.private_subnet_ids
  security_group_ids = [aws_security_group.vpc_endpoints.id]
  # Workspace safety: Set to false to allow multiple devs in one VPC.
  # We pass the specific DNS name to the Lambda via environment variables.
  private_dns_enabled = false

  tags = { Name = "${var.environment}-bedrock-agentcore-endpoint" }
}

# Dedicated endpoint for Gateway MCP traffic
resource "aws_vpc_endpoint" "bedrock_gateway" {
  vpc_id             = local.vpc_id
  service_name       = "com.amazonaws.${var.aws_region}.bedrock-agentcore.gateway"
  vpc_endpoint_type  = "Interface"
  subnet_ids         = local.private_subnet_ids
  security_group_ids = [aws_security_group.vpc_endpoints.id]

  # KEEP this as false for multi-dev use
  private_dns_enabled = false

  tags = { Name = "${var.environment}-bedrock-gateway-endpoint" }
}
