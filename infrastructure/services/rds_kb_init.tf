## INITIALISATION LOGIC: KNOWLEDGE BASE
# Triggers the KB initialisation Lambda whenever the KB configuration changes.

locals {
  kb_config = yamldecode(file("${path.module}/kb_config.yaml"))
}

resource "terraform_data" "rds_kb_init_trigger" {
  input = {
    config_hash = filemd5("${path.module}/kb_config.yaml")
  }

  triggers_replace = [
    filemd5("${path.module}/kb_config.yaml")
  ]

  provisioner "local-exec" {
    command = "aws lambda invoke --function-name ${aws_lambda_function.rds_kb_init.function_name} --cli-binary-format raw-in-base64-out --region ${var.aws_region} --cli-read-timeout 300 response_kb_init.json"
  }

  depends_on = [
    aws_db_instance.dept_contacts_metadata,
    aws_lambda_function.rds_kb_init
  ]
}
