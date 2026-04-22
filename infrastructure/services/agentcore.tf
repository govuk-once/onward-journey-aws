## MANAGED BEDROCK AGENTCORE CAPABILITIES
# Defines the modular utilities used by the Orchestration Layer. Offloads state management and tool connectivity to AWS-managed services.

## AGENTCORE MEMORY
# Managed storage for chat history and session context.
# LangGraph will call this to persist conversation state.
resource "aws_bedrockagentcore_memory" "agent_chat_context" {
  name                      = "${var.environment}_agent_chat_context"
  memory_execution_role_arn = aws_iam_role.agentcore_role.arn
  event_expiry_duration     = 30
}

## AGENTCORE GATEWAY
# Standardised interface for tool connectivity via MCP. Acts as the bridge between the Orchestrator and external data sources.
resource "aws_bedrockagentcore_gateway" "tool_interface" {
  name            = "${var.environment}-tool-interface"
  role_arn        = aws_iam_role.agentcore_role.arn
  protocol_type   = "MCP"
  authorizer_type = "AWS_IAM"
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
    aws_iam_role_policy.agentcore_gateway_invocation,
    aws_lambda_permission.allow_bedrock_gateway_crm
  ]
}
