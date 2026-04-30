## DATABASE SEEDING REGISTRY
# Maps specific source files to their target RDS tables.
# To add a new data source, simply add a key-value pair to this map.

locals {
  # Parse the YAML file into a Terraform object
  seed_config = yamldecode(file("${path.module}/seed_config.yaml"))

  # Dynamically build the trigger map: { "dept_contacts" = { file = "mock_rag_data.csv", name = "dept_contacts" } }
  # Includes ALL tables from the YAML to ensure schema-only tables are also created.
  data_sources = {
    for table in local.seed_config.tables : table.name => {
      file_name  = lookup(table, "source_file", null)
      table_name = table.name
    }
  }
}

## RE-SEEDING LOGIC
# Monitors file hashes and triggers a targeted Lambda invocation only on change.
resource "terraform_data" "rds_sync_trigger" {
  for_each = local.data_sources

  input = each.value

  triggers_replace = [
    # Re-seed if the hash of the local file changes (if a file is defined)
    each.value.file_name != null ? filemd5("${path.module}/mock_data/${each.value.file_name}") : "none",
    # Re-seed if the YAML definition changes
    filemd5("${path.module}/seed_config.yaml")
  ]

  # Targeted invocation: passing the specific file and table as a JSON payload
  provisioner "local-exec" {
    command = "aws lambda invoke --function-name ${aws_lambda_function.rds_seeder.function_name} --cli-binary-format raw-in-base64-out --payload '${jsonencode(self.input)}' --region ${var.aws_region} --cli-read-timeout 300 response_${each.key}.json"
  }

  # Ensures S3, RDS and Lambda are fully ready before attempting to seed
  depends_on = [
    aws_db_instance.dept_contacts_metadata,
    aws_s3_object.mock_data_upload,
    aws_lambda_function.rds_seeder
  ]
}
