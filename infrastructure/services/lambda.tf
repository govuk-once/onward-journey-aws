locals {
  shared_layer_configs = {
    core = {
      name        = "core-logic"
      description = "Core logic and dependencies"
      zip_path    = local.core_output_zip
      trigger     = local.core_trigger_hash
    }
    integrations = {
      name        = "integrations-logic"
      description = "CRM integrations logic"
      zip_path    = local.integrations_output_zip
      trigger     = local.integrations_trigger_hash
    }
  }
}

## SHARED LAYERS
resource "aws_lambda_layer_version" "shared_layers" {
  for_each   = local.shared_layer_configs
  filename   = each.value.zip_path
  layer_name = "${var.environment}-${each.value.name}"

  # We use the hash of source files as the description to trigger new versions.
  # Normally, 'source_code_hash' would be used, but since zip files are generated
  # by local-exec (null_resource) during the Apply phase, using 'source_code_hash'
  # would cause 'terraform plan' to crash because the file doesn't exist yet.
  description = "${each.value.description}. Build hash: ${each.value.trigger}"

  compatible_runtimes = ["python3.12"]

  # Ensure the build process completes before we try to upload the layer.
  # IMPORTANT: If you add a new layer to shared_layer_configs, you MUST add its
  # build resource (null_resource) to this list.
  depends_on = [null_resource.build_core_layer, null_resource.build_integrations_layer]
}

## DATA INGESTION: RDS SEEDER
resource "aws_cloudwatch_log_group" "rds_seeder" {
  name              = "/aws/lambda/${var.environment}-rds-seeder"
  retention_in_days = 14
}

resource "aws_lambda_function" "rds_seeder" {
  filename         = data.archive_file.rds_seeder_zip.output_path
  source_code_hash = data.archive_file.rds_seeder_zip.output_base64sha256
  function_name    = "${var.environment}-rds-seeder"
  role             = aws_iam_role.rds_seeder_role.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  layers           = [aws_lambda_layer_version.shared_layers["core"].arn]
  memory_size      = 512
  timeout          = 900

  vpc_config {
    subnet_ids         = local.private_subnet_ids
    security_group_ids = [aws_security_group.rds_seeder_sg.id]
  }

  environment {
    variables = {
      DB_CONFIG                = jsonencode(local.seed_config)
      DB_HOST                  = aws_db_instance.dept_contacts_metadata.address
      DB_NAME                  = aws_db_instance.dept_contacts_metadata.db_name
      DB_USER                  = aws_db_instance.dept_contacts_metadata.username
      DB_SECRET_ARN            = data.aws_secretsmanager_secret_version.dept_contacts_db_password.arn
      SECRETS_ENDPOINT_URL     = aws_vpc_endpoint.secrets.dns_entry[0]["dns_name"]
      BEDROCK_RUNTIME_ENDPOINT = aws_vpc_endpoint.bedrock.dns_entry[0]["dns_name"]
      LAMBDA_ENDPOINT_URL      = aws_vpc_endpoint.lambda.dns_entry[0]["dns_name"]
      BUCKET_NAME              = aws_s3_bucket.dataset_storage.id
      CRM_TOOL_LAMBDA_ARN      = aws_lambda_function.crm_tool.arn
    }
  }

  depends_on = [aws_cloudwatch_log_group.rds_seeder]
}


## ORCHESTRATION LAYER
# This function manages the LangGraph state machine and coordinates tool calls.
resource "aws_cloudwatch_log_group" "orchestrator" {
  name              = "/aws/lambda/${var.environment}-orchestrator"
  retention_in_days = 14
}

resource "aws_lambda_function" "orchestrator" {
  filename         = data.archive_file.orchestrator_zip.output_path
  source_code_hash = data.archive_file.orchestrator_zip.output_base64sha256
  function_name    = "${var.environment}-orchestrator"
  role             = aws_iam_role.inference.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  layers           = [aws_lambda_layer_version.shared_layers["core"].arn]
  memory_size      = 1024
  timeout          = 120

  vpc_config {
    subnet_ids         = local.private_subnet_ids
    security_group_ids = [aws_security_group.orchestrator.id]
  }

  environment {
    variables = {
      ENV_PREFIX = var.environment
      # URL for AgentCore
      AGENT_RUNTIME_ENDPOINT_URL = aws_vpc_endpoint.bedrock_agentcore.dns_entry[0]["dns_name"]
      # URL for Claude (Inference)
      BEDROCK_RUNTIME_ENDPOINT = aws_vpc_endpoint.bedrock.dns_entry[0]["dns_name"]
      # URL for Secrets Manager
      SECRETS_ENDPOINT_URL = aws_vpc_endpoint.secrets.dns_entry[0]["dns_name"]
      # Specific DNS for the Gateway Endpoint
      GATEWAY_ENDPOINT_URL = aws_vpc_endpoint.bedrock_gateway.dns_entry[0]["dns_name"]
      GATEWAY_URL          = "https://${aws_bedrockagentcore_gateway.tool_interface.gateway_id}.gateway.bedrock-agentcore.${var.aws_region}.amazonaws.com/mcp"
      MEMORY_ID            = aws_bedrockagentcore_memory.agent_chat_context.id
    }
  }

  depends_on = [aws_cloudwatch_log_group.orchestrator]
}


## TOOL LAYER: RDS SEARCH TOOL (MCP SERVER)
# Standardised interface for the Orchestrator to query the registry via Gateway.
resource "aws_cloudwatch_log_group" "rds_tool" {
  name              = "/aws/lambda/${var.environment}-rds-tool"
  retention_in_days = 14
}

resource "aws_lambda_function" "rds_tool" {
  filename         = data.archive_file.rds_tool_zip.output_path
  source_code_hash = data.archive_file.rds_tool_zip.output_base64sha256
  function_name    = "${var.environment}-rds-tool"
  role             = aws_iam_role.rds_tool_role.arn # Requires access to Bedrock and RDS
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  layers           = [aws_lambda_layer_version.shared_layers["core"].arn]
  memory_size      = 512
  timeout          = 120

  vpc_config {
    subnet_ids         = local.private_subnet_ids
    security_group_ids = [aws_security_group.rds_tool_sg.id]
  }

  environment {
    variables = {
      DB_HOST                  = aws_db_instance.dept_contacts_metadata.address
      DB_NAME                  = aws_db_instance.dept_contacts_metadata.db_name
      DB_USER                  = "rds_readonly_dept_contacts"
      SECRETS_ENDPOINT_URL     = aws_vpc_endpoint.secrets.dns_entry[0]["dns_name"]
      BEDROCK_RUNTIME_ENDPOINT = aws_vpc_endpoint.bedrock.dns_entry[0]["dns_name"]
    }
  }

  depends_on = [aws_cloudwatch_log_group.rds_tool]
}

## TOOL LAYER: CRM CONTACT TOOL (MCP SERVER)
# Configured as a "Public" Lambda (outside VPC) to allow external API access for testing.
resource "aws_cloudwatch_log_group" "crm_tool" {
  name              = "/aws/lambda/${var.environment}-crm-tool"
  retention_in_days = 14
}

resource "aws_lambda_function" "crm_tool" {
  filename         = data.archive_file.crm_tool_zip.output_path
  source_code_hash = data.archive_file.crm_tool_zip.output_base64sha256
  function_name    = "${var.environment}-crm-tool"
  role             = aws_iam_role.crm_tool_role.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  layers           = [aws_lambda_layer_version.shared_layers["core"].arn, aws_lambda_layer_version.shared_layers["integrations"].arn]
  memory_size      = 512
  timeout          = 30

  environment {
    variables = {
      ENV_PREFIX = var.environment
    }
  }

  depends_on = [aws_cloudwatch_log_group.crm_tool]
}

## ORCHESTRATOR STREAMING ENDPOINT
# Enables the RESPONSE_STREAM mode for real-time interaction with the Svelte frontend.
resource "aws_lambda_function_url" "orchestrator_url" {
  function_name      = aws_lambda_function.orchestrator.function_name
  authorization_type = "AWS_IAM"
  invoke_mode        = "RESPONSE_STREAM"

  cors {
    allow_credentials = true
    # TODO: Restrict to your specific frontend domain in production
    allow_origins = [
      "http://localhost:5173",
      "https://main.${aws_amplify_app.frontend.id}.amplifyapp.com",
    ]
    allow_methods = ["POST"]
    # SigV4 signing headers required in addition to content-type
    allow_headers = ["content-type", "x-amz-date", "x-amz-security-token", "authorization", "x-amz-content-sha256"]
    max_age       = 86400 # Cache permission for 24 hours (86400 seconds) to prevent lag
  }
}

output "orchestrator_url" {
  description = "The streaming HTTP endpoint for the Orchestrator"
  value       = aws_lambda_function_url.orchestrator_url.function_url
}

## RDS INIT
resource "aws_cloudwatch_log_group" "rds_init" {
  name              = "/aws/lambda/${var.environment}-rds-init"
  retention_in_days = 14
}

resource "aws_lambda_function" "rds_init" {
  filename         = data.archive_file.rds_init_zip.output_path
  source_code_hash = data.archive_file.rds_init_zip.output_base64sha256
  function_name    = "${var.environment}-rds-init"
  role             = aws_iam_role.rds_init_role.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  layers           = [aws_lambda_layer_version.shared_layers["core"].arn]
  memory_size      = 512
  timeout          = 300

  vpc_config {
    subnet_ids         = local.private_subnet_ids
    security_group_ids = [aws_security_group.rds_init_sg.id]
  }

  environment {
    variables = {
      KB_CONFIG            = jsonencode(local.kb_config)
      DB_HOST              = aws_db_instance.dept_contacts_metadata.address
      DB_NAME              = aws_db_instance.dept_contacts_metadata.db_name
      DB_USER              = aws_db_instance.dept_contacts_metadata.username
      DB_SECRET_ARN        = data.aws_secretsmanager_secret_version.dept_contacts_db_password.arn
      SECRETS_ENDPOINT_URL = aws_vpc_endpoint.secrets.dns_entry[0]["dns_name"]
    }
  }

  depends_on = [aws_cloudwatch_log_group.rds_init]
}
