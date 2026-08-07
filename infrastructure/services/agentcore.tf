## MANAGED BEDROCK AGENTCORE CAPABILITIES
# Defines the modular utilities used by the Orchestration Layer. Offloads state management and tool connectivity to AWS-managed services.

## AGENTCORE MEMORY
# Managed storage for chat history and session context.
# LangGraph will call this to persist conversation state.
resource "aws_bedrockagentcore_memory" "agent_chat_context" {
  name                      = "${var.environment}_agent_chat_context"
  memory_execution_role_arn = aws_iam_role.agentcore_role.arn
  event_expiry_duration     = 30

  depends_on = [
    aws_iam_role_policy.agentcore_gateway_invocation
  ]
}

## AGENTCORE GATEWAY
# Standardised interface for tool connectivity via MCP. Acts as the bridge between the Orchestrator and external data sources.
resource "aws_bedrockagentcore_gateway" "tool_interface" {
  name            = "${var.environment}-tool-interface"
  role_arn        = aws_iam_role.agentcore_role.arn
  protocol_type   = "MCP"
  authorizer_type = "AWS_IAM"

  depends_on = [
    aws_iam_role_policy.agentcore_gateway_invocation
  ]
}


## AGENTCORE GATEWAY TARGET: RDS SEARCH TOOL
# Registers the RDS Tool Lambda as a discoverable tool for the Orchestrator.
resource "aws_bedrockagentcore_gateway_target" "rds_search_tool" {
  name               = "${var.environment}-rds-search-tool"
  gateway_identifier = aws_bedrockagentcore_gateway.tool_interface.gateway_id

  target_configuration {
    mcp {
      lambda {
        lambda_arn = aws_lambda_function.rds_tool.arn
        tool_schema {
          inline_payload {
            name        = "query_department_database"
            description = "Searches the department contacts database using semantic vector search."

            input_schema {
              type        = "object"
              description = "Input for the department contact search tool"

              property {
                name        = "query"
                type        = "string"
                description = "The user query to search for contact info."
                required    = true
              }
            }
          }
          inline_payload {
            name        = "query_knowledge_base"
            description = "Searches the department knowledge base for policy and how-to articles."

            input_schema {
              type        = "object"
              description = "Input for the knowledge base search tool"

              property {
                name        = "query"
                type        = "string"
                description = "The search query for the knowledge base."
                required    = true
              }
              property {
                name        = "kb_identifier"
                type        = "string"
                description = "The unique identifier for the department knowledge base."
                required    = true
              }
            }
          }
        }
      }
    }
  }

  credential_provider_configuration {
    gateway_iam_role {} # Use the Gateway's role to invoke the Lambda
  }
  depends_on = [
    time_sleep.wait_for_iam_propagation,
    aws_iam_role_policy.agentcore_gateway_invocation,
    aws_lambda_permission.allow_bedrock_gateway
  ]
}


### AGENTCORE GATEWAY TARGET: CRM LIVE CHAT QUEUE AVAILABILITY
resource "aws_bedrockagentcore_gateway_target" "crm_availability" {
  name               = "${var.environment}-crm-availability"
  gateway_identifier = aws_bedrockagentcore_gateway.tool_interface.gateway_id

  target_configuration {
    mcp {
      lambda {
        lambda_arn = aws_lambda_function.crm_tool.arn
        tool_schema {
          inline_payload {
            name        = "check_chat_availability"
            description = "Checks if human advisers are online and gets the estimated wait time."
            input_schema {
              type = "object"
              property {
                name        = "live_chat_identifier"
                type        = "string"
                description = "The unique ID for the department's chat queue."
                required    = true
              }
            }
          }
        }
      }
    }
  }
  credential_provider_configuration {
    gateway_iam_role {}
  }
  depends_on = [
    time_sleep.wait_for_iam_propagation,
    aws_iam_role_policy.agentcore_gateway_invocation,
    aws_lambda_permission.allow_bedrock_gateway_crm
  ]
}

## AGENTCORE GATEWAY TARGET: CRM HANDOFF
resource "aws_bedrockagentcore_gateway_target" "crm_handoff" {
  name               = "${var.environment}-crm-handoff"
  gateway_identifier = aws_bedrockagentcore_gateway.tool_interface.gateway_id

  target_configuration {
    mcp {
      lambda {
        lambda_arn = aws_lambda_function.crm_tool.arn
        tool_schema {
          inline_payload {
            name        = "connect_to_live_chat"
            description = "Initiates a handoff to a human adviser with a summary of the conversation."
            input_schema {
              type = "object"
              property {
                name        = "live_chat_identifier"
                type        = "string"
                description = "The unique ID for the department's chat queue."
                required    = true
              }
              property {
                name        = "reason"
                type        = "string"
                description = "Short reason for the handoff. You MUST provide a professional string here (e.g., 'Assistance with driving licence renewal')."
                required    = true
              }
              property {
                name        = "summary"
                type        = "string"
                description = "A 2-3 sentence briefing note for the human adviser. You MUST provide a string focusing on the current user's unresolved issue or query."
                required    = true
              }
            }
          }
        }
      }
    }
  }
  credential_provider_configuration {
    gateway_iam_role {}
  }
  depends_on = [
    time_sleep.wait_for_iam_propagation,
    aws_iam_role_policy.agentcore_gateway_invocation,
    aws_lambda_permission.allow_bedrock_gateway_crm
  ]
}

# -------------------------------------------------------------------------
# AGENTCORE RUNTIME INFRASTRUCTURE
# -------------------------------------------------------------------------

# S3 Upload & AgentCore Runtime Provisioning
# AgentCore Runtime requires the ZIP to be hosted in S3 for Direct Code Deployment.
resource "aws_s3_object" "agentcore_runtime_deployment_zip" {
  bucket      = aws_s3_bucket.dataset_storage.id
  key         = "deployments/agentcore_runtime_payload.zip"
  source      = abspath("${path.module}/../../dist/agentcore_payload.zip")
  source_hash = local.agentcore_trigger_hash

  # Wait for the local build script to finish before uploading
  depends_on = [null_resource.build_agentcore_payload]
}

# Provision the actual AgentCore execution environment
resource "aws_bedrockagentcore_agent_runtime" "orchestrator_runtime" {
  # Name must not contain hyphens (including the var.environment variable)
  agent_runtime_name = "${var.environment}_agentcore_runtime_orchestrator"
  role_arn           = aws_iam_role.agentcore_runtime_execution_role.arn

  # AgentCore relies on an artifact block to pull the S3 hosted payload
  agent_runtime_artifact {
    code_configuration {
      runtime     = "PYTHON_3_12"
      entry_point = ["orchestrator.py"]

      code {
        s3 {
          bucket = aws_s3_object.agentcore_runtime_deployment_zip.bucket
          prefix = aws_s3_object.agentcore_runtime_deployment_zip.key
        }
      }
    }
  }

  # Maps the runtime to private subnets so it can access RDS and VPC endpoints
  network_configuration {
    network_mode = "VPC"
    network_mode_config {
      subnets = local.private_subnet_ids
      # Reusing existing orchestrator security group
      security_groups = [aws_security_group.orchestrator.id]
    }
  }

  environment_variables = {
    ENV_PREFIX                 = var.environment
    AGENT_RUNTIME_ENDPOINT_URL = aws_vpc_endpoint.bedrock_agentcore.dns_entry[0]["dns_name"]
    BEDROCK_RUNTIME_ENDPOINT   = aws_vpc_endpoint.bedrock.dns_entry[0]["dns_name"]
    SECRETS_ENDPOINT_URL       = aws_vpc_endpoint.secrets.dns_entry[0]["dns_name"]
    GATEWAY_ENDPOINT_URL       = aws_vpc_endpoint.bedrock_gateway.dns_entry[0]["dns_name"]
    GATEWAY_URL                = "https://${aws_bedrockagentcore_gateway.tool_interface.gateway_id}.gateway.bedrock-agentcore.${var.aws_region}.amazonaws.com/mcp"
    MEMORY_ID                  = aws_bedrockagentcore_memory.agent_chat_context.id

    # Injecting the hash forces AgentCore to pull the new ZIP when the code changes
    _DEPLOYMENT_HASH = aws_s3_object.agentcore_runtime_deployment_zip.source_hash
  }
}
