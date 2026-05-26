## INITIALISATION LOGIC: RDS
# Triggers the RDS initialisation Lambda whenever the KB configuration changes.

locals {
  kb_config = yamldecode(file("${path.module}/kb_config.yaml"))
}

resource "terraform_data" "rds_init_trigger" {
  input = {
    config_hash = filemd5("${path.module}/kb_config.yaml")
    lambda_hash = aws_lambda_function.rds_init.source_code_hash
  }

  triggers_replace = [
    filemd5("${path.module}/kb_config.yaml"),
    aws_lambda_function.rds_init.source_code_hash
  ]

  provisioner "local-exec" {
    command = "aws lambda invoke --function-name ${aws_lambda_function.rds_init.function_name} --cli-binary-format raw-in-base64-out --region ${var.aws_region} --cli-read-timeout 300 response_rds_init.json"
  }

  depends_on = [
    aws_db_instance.dept_contacts_metadata,
    aws_lambda_function.rds_init
  ]
}
