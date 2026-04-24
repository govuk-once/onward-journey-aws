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
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = false

  tags = { Name = "${var.environment}-bedrock-endpoint" }
}

# 2. Secrets Manager Endpoint - Required to retrieve the DB password
resource "aws_vpc_endpoint" "secrets" {
  vpc_id            = local.vpc_id
  service_name      = "com.amazonaws.${var.aws_region}.secretsmanager"
  vpc_endpoint_type = "Interface"

  subnet_ids          = local.private_subnet_ids
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = false

  tags = { Name = "${var.environment}-secrets-endpoint" }
}

# # 3. S3 Endpoint (Gateway) - Required to ingest the contacts CSV
# # Run the dynamic check script
# data "external" "s3_check" {
#   program = ["sh", "${path.module}/check_s3_gateway.sh", local.vpc_id, var.aws_region, var.environment]
# }

# # Prevent destruction
# moved {
#   from = aws_vpc_endpoint.s3
#   to   = aws_vpc_endpoint.s3[0]
# }

# # Only create the S3 Gateway if the script says it doesn't exist
# resource "aws_vpc_endpoint" "s3" {
#   # Logic: Create it ONLY if I'm not already the owner AND no one else has one.
#   # OR: If I AM the owner, keep it (count=1).
#   count = (data.external.s3_check.result.is_owner == "true" || data.external.s3_check.result.id == "None") ? 1 : 0

#   vpc_id            = local.vpc_id
#   service_name      = "com.amazonaws.${var.aws_region}.s3"
#   vpc_endpoint_type = "Gateway"

#   # Link to the private route tables
#   route_table_ids = local.private_route_table_ids

#   tags = { Name = "${var.environment}-s3-gateway" }
# }

# # Associate your route tables with the Gateway (whichever one exists)
# resource "aws_vpc_endpoint_route_table_association" "s3_routing" {
#   for_each       = toset(local.private_route_table_ids)
#   route_table_id = each.value

#   # Use my ID if I created/own it, otherwise use the shared ID found by the script
#   vpc_endpoint_id = length(aws_vpc_endpoint.s3) > 0 ? aws_vpc_endpoint.s3[0].id : data.external.s3_check.result.id
# }

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
