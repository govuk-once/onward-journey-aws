/**
 * PURPOSE: Knowledge Base Synchronisation Pipeline Lambdas.
 * Handles metadata checks, article fetching, embedding generation, database upserts,
 * and cleanup tasks for the KB sync workflow.
 */

locals {
  kb_sync_lambdas = {
    # 1. Check KB Metadata (Public CRM Access)
    check_kb_meta = {
      function_name = "${var.environment}-kb-sync-check-kb-meta"
      archive_path  = data.archive_file.kb_sync_check_kb_meta_zip.output_path
      archive_hash  = data.archive_file.kb_sync_check_kb_meta_zip.output_base64sha256
      role_arn      = aws_iam_role.kb_sync_crm_role.arn
      memory_size   = 512
      timeout       = 30
      in_vpc        = false
      env_vars = {
        ENV_PREFIX = var.environment
      }
    }

    # 2. Check Sync Metadata (VPC DB Access)
    check_sync_meta = {
      function_name = "${var.environment}-kb-sync-check-sync-meta"
      archive_path  = data.archive_file.kb_sync_check_sync_meta_zip.output_path
      archive_hash  = data.archive_file.kb_sync_check_sync_meta_zip.output_base64sha256
      role_arn      = aws_iam_role.kb_sync_role.arn
      memory_size   = 512
      timeout       = 30
      in_vpc        = true
      env_vars = {
        CONTACTS_TABLE_NAME  = local.active_contacts_table
        DB_HOST              = aws_db_instance.dept_contacts_metadata.address
        DB_NAME              = aws_db_instance.dept_contacts_metadata.db_name
        DB_USER              = "rds_readonly_dept_contacts"
        SECRETS_ENDPOINT_URL = aws_vpc_endpoint.endpoints["secrets"].dns_entry[0]["dns_name"]
        ENV_PREFIX           = var.environment
      }
    }

    # 3. Fetch Articles (Public CRM Access)
    fetch_articles = {
      function_name = "${var.environment}-kb-sync-fetch-articles"
      archive_path  = data.archive_file.kb_sync_fetch_articles_zip.output_path
      archive_hash  = data.archive_file.kb_sync_fetch_articles_zip.output_base64sha256
      role_arn      = aws_iam_role.kb_sync_crm_role.arn
      memory_size   = 512
      timeout       = 60
      in_vpc        = false
      env_vars = {
        ENV_PREFIX = var.environment
      }
    }

    # 4. Upsert Knowledge Data (VPC Bedrock + DB Access)
    upsert = {
      function_name = "${var.environment}-kb-sync-upsert"
      archive_path  = data.archive_file.kb_sync_upsert_zip.output_path
      archive_hash  = data.archive_file.kb_sync_upsert_zip.output_base64sha256
      role_arn      = aws_iam_role.kb_sync_role.arn
      memory_size   = 1024
      timeout       = 30
      in_vpc        = true
      env_vars = {
        DB_HOST                  = aws_db_instance.dept_contacts_metadata.address
        DB_NAME                  = aws_db_instance.dept_contacts_metadata.db_name
        DB_USER                  = aws_db_instance.dept_contacts_metadata.username
        DB_SECRET_ARN            = data.aws_secretsmanager_secret_version.dept_contacts_db_password.arn
        SECRETS_ENDPOINT_URL     = aws_vpc_endpoint.endpoints["secrets"].dns_entry[0]["dns_name"]
        BEDROCK_RUNTIME_ENDPOINT = aws_vpc_endpoint.endpoints["bedrock"].dns_entry[0]["dns_name"]
      }
    }

    # 5. Update Sync Metadata (VPC DB Access)
    update_sync_meta = {
      function_name = "${var.environment}-kb-sync-update-sync-meta"
      archive_path  = data.archive_file.kb_sync_update_sync_meta_zip.output_path
      archive_hash  = data.archive_file.kb_sync_update_sync_meta_zip.output_base64sha256
      role_arn      = aws_iam_role.kb_sync_role.arn
      memory_size   = 512
      timeout       = 30
      in_vpc        = true
      env_vars = {
        DB_HOST              = aws_db_instance.dept_contacts_metadata.address
        DB_NAME              = aws_db_instance.dept_contacts_metadata.db_name
        DB_USER              = aws_db_instance.dept_contacts_metadata.username
        DB_SECRET_ARN        = data.aws_secretsmanager_secret_version.dept_contacts_db_password.arn
        SECRETS_ENDPOINT_URL = aws_vpc_endpoint.endpoints["secrets"].dns_entry[0]["dns_name"]
      }
    }

    # 6. Cleanup Unmapped Records (VPC DB Access)
    cleanup_unmapped = {
      function_name = "${var.environment}-kb-sync-cleanup-unmapped"
      archive_path  = data.archive_file.kb_sync_cleanup_unmapped_zip.output_path
      archive_hash  = data.archive_file.kb_sync_cleanup_unmapped_zip.output_base64sha256
      role_arn      = aws_iam_role.kb_sync_cleanup_role.arn
      memory_size   = 512
      timeout       = 30
      in_vpc        = true
      env_vars = {
        DB_HOST              = aws_db_instance.dept_contacts_metadata.address
        DB_NAME              = aws_db_instance.dept_contacts_metadata.db_name
        DB_USER              = aws_db_instance.dept_contacts_metadata.username
        DB_SECRET_ARN        = data.aws_secretsmanager_secret_version.dept_contacts_db_password.arn
        SECRETS_ENDPOINT_URL = aws_vpc_endpoint.endpoints["secrets"].dns_entry[0]["dns_name"]
      }
    }
  }
}

# ==============================================================================
# LOG GROUPS
# ==============================================================================
resource "aws_cloudwatch_log_group" "kb_sync" {
  for_each = local.kb_sync_lambdas

  name              = "/aws/lambda/${each.value.function_name}"
  retention_in_days = 14

  tags = {
    Component = "kb-sync"
  }
}

# ==============================================================================
# LAMBDA FUNCTIONS
# ==============================================================================
resource "aws_lambda_function" "kb_sync" {
  for_each = local.kb_sync_lambdas

  filename         = each.value.archive_path
  source_code_hash = each.value.archive_hash
  function_name    = each.value.function_name
  role             = each.value.role_arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  layers           = [aws_lambda_layer_version.shared_layers["core"].arn, aws_lambda_layer_version.shared_layers["integrations"].arn]
  memory_size      = each.value.memory_size
  timeout          = each.value.timeout
  architectures    = ["arm64"]

  dynamic "vpc_config" {
    for_each = each.value.in_vpc ? [1] : []
    content {
      subnet_ids         = local.private_subnet_ids
      security_group_ids = [aws_security_group.kb_sync_sg.id]
    }
  }

  environment {
    variables = each.value.env_vars
  }

  depends_on = [aws_cloudwatch_log_group.kb_sync]

  tags = {
    Component = "kb-sync"
  }
}

# ==============================================================================
# STATE MIGRATION GUARDS
# TODO: Safe to remove once this refactor has been applied across active developer workspaces.
# ==============================================================================
moved {
  from = aws_cloudwatch_log_group.kb_sync_check_kb_meta
  to   = aws_cloudwatch_log_group.kb_sync["check_kb_meta"]
}

moved {
  from = aws_lambda_function.kb_sync_check_kb_meta
  to   = aws_lambda_function.kb_sync["check_kb_meta"]
}

moved {
  from = aws_cloudwatch_log_group.kb_sync_check_sync_meta
  to   = aws_cloudwatch_log_group.kb_sync["check_sync_meta"]
}

moved {
  from = aws_lambda_function.kb_sync_check_sync_meta
  to   = aws_lambda_function.kb_sync["check_sync_meta"]
}

moved {
  from = aws_cloudwatch_log_group.kb_sync_fetch_articles
  to   = aws_cloudwatch_log_group.kb_sync["fetch_articles"]
}

moved {
  from = aws_lambda_function.kb_sync_fetch_articles
  to   = aws_lambda_function.kb_sync["fetch_articles"]
}

moved {
  from = aws_cloudwatch_log_group.kb_sync_upsert
  to   = aws_cloudwatch_log_group.kb_sync["upsert"]
}

moved {
  from = aws_lambda_function.kb_sync_upsert
  to   = aws_lambda_function.kb_sync["upsert"]
}

moved {
  from = aws_cloudwatch_log_group.kb_sync_update_sync_meta
  to   = aws_cloudwatch_log_group.kb_sync["update_sync_meta"]
}

moved {
  from = aws_lambda_function.kb_sync_update_sync_meta
  to   = aws_lambda_function.kb_sync["update_sync_meta"]
}

moved {
  from = aws_cloudwatch_log_group.kb_sync_cleanup_unmapped
  to   = aws_cloudwatch_log_group.kb_sync["cleanup_unmapped"]
}

moved {
  from = aws_lambda_function.kb_sync_cleanup_unmapped
  to   = aws_lambda_function.kb_sync["cleanup_unmapped"]
}
