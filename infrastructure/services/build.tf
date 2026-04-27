# This ensures the dist folders exist so the Data Sources don't crash during the Plan phase.
resource "null_resource" "ensure_dist_folders" {
  provisioner "local-exec" {
    command = "mkdir -p ${path.module}/../../dist/layer/python ${path.module}/../../dist/orchestrator_staging ${path.module}/../../dist/rds_seeder_staging ${path.module}/../../dist/rds_tool_staging ${path.module}/../../dist/crm_tool_staging"
  }
}

## SHARED LAMBDA LAYER BUILD
# Consolidates all common dependencies and shared utility logic into a single layer.

locals {
  layer_build_command = <<EOT
    # 1. Clean and prepare fresh staging directory
    rm -rf ${path.module}/../../dist/layer
    mkdir -p ${path.module}/../../dist/layer/python/utils

    # 2. INSTALL DEPENDENCIES
    # We install from pyproject.toml but explicitly EXCLUDE boto3/botocore (pre-installed in Lambda)
    cd ${path.module}/../../app
    uv pip install \
      --target ../dist/layer/python \
      --python-platform x86_64-manylinux_2_28 \
      --python-version 3.12 \
      --only-binary=:all: \
      --link-mode copy \
      --no-cache \
      -r pyproject.toml

    # 3. OPTIMIZE SIZE: Remove boto3, botocore, and cache files to stay under 250MB limit
    rm -rf ${path.module}/../../dist/layer/python/boto3*
    rm -rf ${path.module}/../../dist/layer/python/botocore*
    find ${path.module}/../../dist/layer/python -name "__pycache__" -type d -exec rm -rf {} +
    find ${path.module}/../../dist/layer/python -name "*.pyc" -delete

    # 4. COPY SHARED UTILS
    cp shared/utils/*.py ../dist/layer/python/utils/
  EOT
}

resource "null_resource" "build_shared_layer" {
  triggers = {
    lock_file    = filemd5("${path.module}/../../app/uv.lock")
    shared_logic = sha1(join("", [for f in fileset("${path.module}/../../app/shared/utils/", "*.py") : filemd5("${path.module}/../../app/shared/utils/${f}")]))
    build_script = sha1(local.layer_build_command)
  }

  provisioner "local-exec" {
    command = local.layer_build_command
  }
}

data "archive_file" "shared_layer_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../../dist/layer"
  output_path = "${path.module}/../../dist/shared_layer_payload.zip"

  depends_on = [
    null_resource.build_shared_layer
  ]
}


## INDIVIDUAL LAMBDA PACKAGING (THIN ZIPS)
# Each Lambda now only contains its specific handler.py.

# 1. ORCHESTRATOR
data "archive_file" "orchestrator_zip" {
  type        = "zip"
  output_path = "${path.module}/../../dist/orchestrator_payload.zip"
  source {
    content  = file("${path.module}/../../app/lambdas/orchestrator/handler.py")
    filename = "handler.py"
  }
}

# 2. RDS SEEDER
data "archive_file" "rds_seeder_zip" {
  type        = "zip"
  output_path = "${path.module}/../../dist/rds_seeder_payload.zip"
  source {
    content  = file("${path.module}/../../app/lambdas/rds_seeder/handler.py")
    filename = "handler.py"
  }
}

# 3. RDS TOOL
data "archive_file" "rds_tool_zip" {
  type        = "zip"
  output_path = "${path.module}/../../dist/rds_tool_payload.zip"
  source {
    content  = file("${path.module}/../../app/lambdas/rds_tool/handler.py")
    filename = "handler.py"
  }
}

# 4. CRM TOOL
data "archive_file" "crm_tool_zip" {
  type        = "zip"
  output_path = "${path.module}/../../dist/crm_tool_payload.zip"
  source {
    content  = file("${path.module}/../../app/lambdas/crm_tool/handler.py")
    filename = "handler.py"
  }
}
