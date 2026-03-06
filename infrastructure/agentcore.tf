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
        }
      }
    }
  }

  credential_provider_configuration {
    gateway_iam_role {} # Use the Gateway's role to invoke the Lambda
  }
}


### AGENTCORE GATEWAY TARGET: GENESYS AVAILABILITY
resource "aws_bedrockagentcore_gateway_target" "genesys_availability" {
  name               = "${var.environment}-genesys-availability"
  gateway_identifier = aws_bedrockagentcore_gateway.tool_interface.gateway_id

  target_configuration {
    mcp {
      lambda {
        lambda_arn = aws_lambda_function.genesys_tool.arn
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
}

## AGENTCORE GATEWAY TARGET: GENESYS HANDOFF
resource "aws_bedrockagentcore_gateway_target" "genesys_handoff" {
  name               = "${var.environment}-genesys-handoff"
  gateway_identifier = aws_bedrockagentcore_gateway.tool_interface.gateway_id

  target_configuration {
    mcp {
      lambda {
        lambda_arn = aws_lambda_function.genesys_tool.arn
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
                description = "Short reason for the handoff."
              }
              property {
                name        = "summary"
                type        = "string"
                description = "A 2-3 sentence briefing note for the human adviser."
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
}
