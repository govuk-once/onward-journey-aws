## LAMBDA BUILD PIPELINE: RDS SEEDER
# Automates dependency installation using 'uv' and zipping for the Data Ingestion layer.

resource "null_resource" "install_seeder_deps" {
  triggers = {
    # Re-run if the script changes
    python_code = filemd5("${path.module}/../app/rds_seeder.py")
    # Also trigger if the .tool-versions changes (ensuring python version parity)
    tools_config = filemd5("${path.module}/../.tool-versions")
  }

  provisioner "local-exec" {
    command = <<EOT
      rm -rf ${path.module}/../dist/seeder_staging
      mkdir -p ${path.module}/../dist/seeder_staging
      uv pip install pg8000 --target ${path.module}/../dist/seeder_staging
    EOT
  }
}

resource "local_file" "seeder_script_copy" {
  content    = file("${path.module}/../app/rds_seeder.py")
  filename   = "${path.module}/../dist/seeder_staging/rds_seeder.py"
  depends_on = [null_resource.install_seeder_deps]
}

data "archive_file" "rds_seeder_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../dist/seeder_staging"
  output_path = "${path.module}/../dist/rds_seeder_payload.zip"

  depends_on = [
    null_resource.install_seeder_deps,
    local_file.seeder_script_copy
  ]
}


## LAMBDA BUILD: ORCHESTRATOR
# Prepares a Linux-compatible deployment package by bundling code and binary dependencies.

locals {
  orchestrator_dep_install_command = <<EOT
    # 1. Clean and prepare fresh staging directory
    rm -rf ${path.module}/../dist/orchestrator_staging
    mkdir -p ${path.module}/../dist/orchestrator_staging
    cd ${path.module}/../app

    # 2. COMPILE & INSTALL
    uv pip compile pyproject.toml \
      --python-platform x86_64-manylinux_2_28 \
      --python-version 3.12 \
      --output-file requirements_lambda.txt

    uv pip install \
      --target ../dist/orchestrator_staging \
      --python-platform x86_64-manylinux_2_28 \
      --python-version 3.12 \
      --only-binary=:all: \
      --link-mode copy \
      --no-cache \
      -r requirements_lambda.txt

    # 3. ATOMIC COPY: Copy the script inside the same resource that wipes the folder
    cp orchestrator.py ../dist/orchestrator_staging/

    # 4. Cleanup
    [ -f requirements_lambda.txt ] && rm requirements_lambda.txt
  EOT
}

# 1. DEPENDENCY LAYER: Only runs when libraries or the build logic change.
resource "null_resource" "install_orchestrator_deps" {
  triggers = {
    # Re-run if dependencies OR the script itself changes
    lock_file    = filemd5("${path.module}/../app/uv.lock")
    script_hash  = filemd5("${path.module}/../app/orchestrator.py")
    build_script = sha1(local.orchestrator_dep_install_command)
  }

  provisioner "local-exec" {
    command = local.orchestrator_dep_install_command
  }
}

# 2.  ZIP ARCHIVE
data "archive_file" "orchestrator_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../dist/orchestrator_staging"
  output_path = "${path.module}/../dist/orchestrator_payload.zip"

  depends_on = [
    null_resource.install_orchestrator_deps
  ]
}

## LAMBDA BUILD: RDS TOOL (MCP SERVER)
# Bundles the database search tool with the pure-python pg8000 driver for the Gateway.

resource "null_resource" "install_rds_tool_deps" {
  triggers = {
    # Re-run if the tool logic or dependency lockfile changes
    python_code = filemd5("${path.module}/../app/rds_tool.py")
    lock_file   = filemd5("${path.module}/../app/uv.lock")
  }

  provisioner "local-exec" {
    # 1. Clean staging
    # 2. Install pg8000 (Pure Python, but we force platform for consistency)
    # 3. Copy the updated rds_tool.py
    command = <<EOT
      rm -rf ${path.module}/../dist/rds_tool_staging
      mkdir -p ${path.module}/../dist/rds_tool_staging
      cd ${path.module}/../app

      uv pip install \
        --target ../dist/rds_tool_staging \
        --python-platform x86_64-manylinux_2_28 \
        --python-version 3.12 \
        --no-cache \
        pg8000

      cp rds_tool.py ../dist/rds_tool_staging/
    EOT
  }
}

data "archive_file" "rds_tool_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../dist/rds_tool_staging"
  output_path = "${path.module}/../dist/rds_tool_payload.zip"

  depends_on = [
    null_resource.install_rds_tool_deps
  ]
}

## LAMBDA BUILD: CRM TOOL (MCP SERVER)
# Bundles the CRM API tool with dependencies for external connectivity.

resource "null_resource" "install_crm_tool_deps" {
  triggers = {
    # Re-run if the tool logic changes
    python_code = filemd5("${path.module}/../app/crm_tool.py")
  }

  provisioner "local-exec" {
    command = <<EOT
      rm -rf ${path.module}/../dist/crm_tool_staging
      mkdir -p ${path.module}/../dist/crm_tool_staging
      cd ${path.module}/../app

      uv pip install \
        --target ../dist/crm_tool_staging \
        --python-platform x86_64-manylinux_2_28 \
        --python-version 3.12 \
        --no-cache \
        requests

      cp crm_tool.py ../dist/crm_tool_staging/
    EOT
  }
}

data "archive_file" "crm_tool_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../dist/crm_tool_staging"
  output_path = "${path.module}/../dist/crm_tool_payload.zip"

  depends_on = [
    null_resource.install_crm_tool_deps
  ]
}
