
## KB SYNC: CHECK KB METADATA
resource "aws_cloudwatch_log_group" "kb_sync_check_kb_meta" {
  name              = "/aws/lambda/${var.environment}-kb-sync-check-kb-meta"
  retention_in_days = 14
}

resource "aws_lambda_function" "kb_sync_check_kb_meta" {
  filename         = data.archive_file.kb_sync_check_kb_meta_zip.output_path
  source_code_hash = data.archive_file.kb_sync_check_kb_meta_zip.output_base64sha256
  function_name    = "${var.environment}-kb-sync-check-kb-meta"
  role             = aws_iam_role.crm_tool_role.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  layers           = [aws_lambda_layer_version.shared_logic.arn]
  memory_size      = 512
  timeout          = 30

  environment {
    variables = {
      ENV_PREFIX = var.environment
    }
  }

  depends_on = [aws_cloudwatch_log_group.kb_sync_check_kb_meta]
}

## KB SYNC: CHECK SYNC METADATA
resource "aws_cloudwatch_log_group" "kb_sync_check_sync_meta" {
  name              = "/aws/lambda/${var.environment}-kb-sync-check-sync-meta"
  retention_in_days = 14
}

resource "aws_lambda_function" "kb_sync_check_sync_meta" {
  filename         = data.archive_file.kb_sync_check_sync_meta_zip.output_path
  source_code_hash = data.archive_file.kb_sync_check_sync_meta_zip.output_base64sha256
  function_name    = "${var.environment}-kb-sync-check-sync-meta"
  role             = aws_iam_role.rds_tool_role.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  layers           = [aws_lambda_layer_version.shared_logic.arn]
  memory_size      = 512
  timeout          = 30

  vpc_config {
    subnet_ids         = local.private_subnet_ids
    security_group_ids = [aws_security_group.rds_seeder_sg.id]
  }

  environment {
    variables = {
      DB_HOST              = aws_db_instance.dept_contacts_metadata.address
      DB_NAME              = aws_db_instance.dept_contacts_metadata.db_name
      DB_USER              = aws_db_instance.dept_contacts_metadata.username
      DB_SECRET_ARN        = data.aws_secretsmanager_secret_version.dept_contacts_db_password.arn
      SECRETS_ENDPOINT_URL = aws_vpc_endpoint.secrets.dns_entry[0]["dns_name"]
      ENV_PREFIX           = var.environment
    }
  }

  depends_on = [aws_cloudwatch_log_group.kb_sync_check_sync_meta]
}

## KB SYNC: FETCH ARTICLES
resource "aws_cloudwatch_log_group" "kb_sync_fetch_articles" {
  name              = "/aws/lambda/${var.environment}-kb-sync-fetch-articles"
  retention_in_days = 14
}

resource "aws_lambda_function" "kb_sync_fetch_articles" {
  filename         = data.archive_file.kb_sync_fetch_articles_zip.output_path
  source_code_hash = data.archive_file.kb_sync_fetch_articles_zip.output_base64sha256
  function_name    = "${var.environment}-kb-sync-fetch-articles"
  role             = aws_iam_role.crm_tool_role.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  layers           = [aws_lambda_layer_version.shared_logic.arn]
  memory_size      = 512
  timeout          = 60

  environment {
    variables = {
      ENV_PREFIX = var.environment
    }
  }

  depends_on = [aws_cloudwatch_log_group.kb_sync_fetch_articles]
}

## KB SYNC: UPSERT
resource "aws_cloudwatch_log_group" "kb_sync_upsert" {
  name              = "/aws/lambda/${var.environment}-kb-sync-upsert"
  retention_in_days = 14
}

resource "aws_lambda_function" "kb_sync_upsert" {
  filename         = data.archive_file.kb_sync_upsert_zip.output_path
  source_code_hash = data.archive_file.kb_sync_upsert_zip.output_base64sha256
  function_name    = "${var.environment}-kb-sync-upsert"
  role             = aws_iam_role.rds_tool_role.arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  layers           = [aws_lambda_layer_version.shared_logic.arn]
  memory_size      = 1024
  timeout          = 30

  vpc_config {
    subnet_ids         = local.private_subnet_ids
    security_group_ids = [aws_security_group.rds_seeder_sg.id]
  }

  environment {
    variables = {
      DB_HOST                  = aws_db_instance.dept_contacts_metadata.address
      DB_NAME                  = aws_db_instance.dept_contacts_metadata.db_name
      DB_USER                  = aws_db_instance.dept_contacts_metadata.username
      DB_SECRET_ARN            = data.aws_secretsmanager_secret_version.dept_contacts_db_password.arn
      SECRETS_ENDPOINT_URL     = aws_vpc_endpoint.secrets.dns_entry[0]["dns_name"]
      BEDROCK_RUNTIME_ENDPOINT = aws_vpc_endpoint.bedrock.dns_entry[0]["dns_name"]
    }
  }

  depends_on = [aws_cloudwatch_log_group.kb_sync_upsert]
}
