/**
 * PURPOSE: Private Connectivity (VPC Endpoints).
 * These endpoints allow resources in private subnets to securely
 * communicate with AWS services without traversing the public internet.
 */

locals {
  vpc_endpoints = {
    # 1. Bedrock Endpoint - Required for LLM inference and embeddings
    bedrock = {
      service_suffix  = "bedrock-runtime"
      name            = "${var.environment}-bedrock-endpoint"
      security_groups = [aws_security_group.vpc_endpoints.id, aws_security_group.bedrock.id]
    }

    # 2. Secrets Manager Endpoint - Required to retrieve the DB password
    secrets = {
      service_suffix  = "secretsmanager"
      name            = "${var.environment}-secrets-endpoint"
      security_groups = [aws_security_group.vpc_endpoints.id, aws_security_group.secrets_manager.id]
    }

    # 3. Lambda Endpoint - Required for rds_seeder to invoke crm_tool from within the VPC
    lambda = {
      service_suffix  = "lambda"
      name            = "${var.environment}-lambda-endpoint"
      security_groups = [aws_security_group.vpc_endpoints.id]
    }

    # 4. Bedrock AgentCore Endpoint - Required for Memory/Checkpointer & Gateway
    bedrock_agentcore = {
      service_suffix  = "bedrock-agentcore"
      name            = "${var.environment}-bedrock-agentcore-endpoint"
      security_groups = [aws_security_group.vpc_endpoints.id]
    }

    # 5. Dedicated endpoint for Gateway MCP traffic
    bedrock_gateway = {
      service_suffix  = "bedrock-agentcore.gateway"
      name            = "${var.environment}-bedrock-gateway-endpoint"
      security_groups = [aws_security_group.vpc_endpoints.id]
    }
  }
}

resource "aws_vpc_endpoint" "endpoints" {
  for_each = local.vpc_endpoints

  vpc_id            = local.vpc_id
  service_name      = "com.amazonaws.${var.aws_region}.${each.value.service_suffix}"
  vpc_endpoint_type = "Interface"

  subnet_ids         = local.private_subnet_ids
  security_group_ids = each.value.security_groups

  # Workspace safety: Set to false to allow multiple devs in one VPC.
  # Specific DNS names are passed to Lambdas via environment variables.
  private_dns_enabled = false

  tags = {
    Name      = each.value.name
    Component = "vpc-endpoints"
  }
}

# ==============================================================================
# STATE MIGRATION GUARDS
# TODO: Safe to remove these 'moved' blocks once this refactor has been applied
# across all active developer workspaces.
# ==============================================================================
moved {
  from = aws_vpc_endpoint.bedrock
  to   = aws_vpc_endpoint.endpoints["bedrock"]
}

moved {
  from = aws_vpc_endpoint.secrets
  to   = aws_vpc_endpoint.endpoints["secrets"]
}

moved {
  from = aws_vpc_endpoint.lambda
  to   = aws_vpc_endpoint.endpoints["lambda"]
}

moved {
  from = aws_vpc_endpoint.bedrock_agentcore
  to   = aws_vpc_endpoint.endpoints["bedrock_agentcore"]
}

moved {
  from = aws_vpc_endpoint.bedrock_gateway
  to   = aws_vpc_endpoint.endpoints["bedrock_gateway"]
}
