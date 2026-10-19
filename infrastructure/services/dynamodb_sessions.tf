resource "aws_dynamodb_table" "sessions" {
  name                        = "${var.environment}-sessions"
  billing_mode                = "PAY_PER_REQUEST"
  deletion_protection_enabled = true

  # Primary Partition Key (matches Bedrock AgentCore thread_id / session_id)
  hash_key = "session_id"

  # Attribute type definitions
  # Note: Only key attributes (PK, SK, GSI keys) are declared in DynamoDB attribute blocks
  attribute {
    name = "session_id"
    type = "S"
  }

  attribute {
    name = "crm_chat_id"
    type = "S"
  }

  # Global Secondary Index for Content Guru Storm webhook lookups
  global_secondary_index {
    name            = "crm_chat_id-index"
    projection_type = "ALL"

    key_schema {
      attribute_name = "crm_chat_id"
      key_type       = "HASH"
    }
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }

  # Component-level override; base tags (Product, Service, Environment, Owner, Source, PipelineStackName)
  # are inherited automatically from provider default_tags
  tags = {
    Component = "session-storage"
  }
}
