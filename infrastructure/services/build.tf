# This ensures the dist folders exist so the Data Sources don't crash during the Plan phase.
resource "null_resource" "ensure_dist_folders" {
  provisioner "local-exec" {
    command = "mkdir -p ${path.module}/../../dist/layer/python ${path.module}/../../dist/orchestrator_staging ${path.module}/../../dist/rds_seeder_staging ${path.module}/../../dist/rds_tool_staging ${path.module}/../../dist/crm_tool_staging ${path.module}/../../dist/kb_sync_staging ${path.module}/../../dist/rds_init_staging"
  }
}

## SHARED LAMBDA LAYER BUILD
# Consolidates all common dependencies and shared utility logic into a single layer.

locals {
  layer_build_command = <<EOT
    set -e
    echo "Starting Lambda Layer build..."

    # 1. Prepare staging directory
    STAGING_DIR="${path.module}/../../dist/layer"
    rm -rf "$STAGING_DIR"
    mkdir -p "$STAGING_DIR/python"

    # 2. INSTALL DEPENDENCIES
    echo "Installing external dependencies from pyproject.toml..."
    cd "${path.module}/../../app"

    # We use uv to install dependencies into the layer's python directory.
    # We explicitly target the Lambda runtime platform (Amazon Linux 2023 / manylinux_2_28).
    uv pip install \
      --target "../dist/layer/python" \
      --python-platform x86_64-manylinux_2_28 \
      --python-version 3.12 \
      --only-binary=:all: \
      --link-mode copy \
      --no-cache \
      -r pyproject.toml

    # 3. OPTIMISE SIZE
    echo "Cleaning up pre-installed and temporary files..."
    rm -rf "$STAGING_DIR/python/boto3"*
    rm -rf "$STAGING_DIR/python/botocore"*
    find "$STAGING_DIR/python" -name "__pycache__" -type d -exec rm -rf {} +
    find "$STAGING_DIR/python" -name "*.pyc" -delete

    # 4. COPY SHARED DIRECTORIES (Internal utilities)
    echo "Copying shared internal utilities..."
    mkdir -p "$STAGING_DIR/python/utils"
    mkdir -p "$STAGING_DIR/python/integrations"
    cp shared/utils/*.py "$STAGING_DIR/python/utils/"
    cp shared/integrations/*.py "$STAGING_DIR/python/integrations/"
    mkdir -p "$STAGING_DIR/python/integrations/providers"
    cp shared/integrations/providers/*.py "$STAGING_DIR/python/integrations/providers/"

    echo "Lambda Layer build complete."
    ls -F "$STAGING_DIR/python"
    EOT
}


resource "null_resource" "build_shared_layer" {
  triggers = {
    lock_file    = filemd5("${path.module}/../../app/uv.lock")
    shared_utils = sha1(join("", [for f in fileset("${path.module}/../../app/shared/utils/", "**/*.py") : filemd5("${path.module}/../../app/shared/utils/${f}")]))
    integrations = sha1(join("", [for f in fileset("${path.module}/../../app/shared/integrations/", "**/*.py") : filemd5("${path.module}/../../app/shared/integrations/${f}")]))
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


## INDIVIDUAL LAMBDA PACKAGING

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

# 5. KB SYNC: CHECK KB METADATA
data "archive_file" "kb_sync_check_kb_meta_zip" {
  type        = "zip"
  output_path = "${path.module}/../../dist/kb_sync_check_kb_meta_payload.zip"
  source {
    content  = file("${path.module}/../../app/lambdas/kb_sync/check_kb_meta/handler.py")
    filename = "handler.py"
  }
}

# 6. KB SYNC: CHECK SYNC METADATA
data "archive_file" "kb_sync_check_sync_meta_zip" {
  type        = "zip"
  output_path = "${path.module}/../../dist/kb_sync_check_sync_meta_payload.zip"
  source {
    content  = file("${path.module}/../../app/lambdas/kb_sync/check_sync_meta/handler.py")
    filename = "handler.py"
  }
}

# 7. KB SYNC: FETCH ARTICLES
data "archive_file" "kb_sync_fetch_articles_zip" {
  type        = "zip"
  output_path = "${path.module}/../../dist/kb_sync_fetch_articles_payload.zip"
  source {
    content  = file("${path.module}/../../app/lambdas/kb_sync/fetch_articles/handler.py")
    filename = "handler.py"
  }
}

# 8. KB SYNC: UPSERT
data "archive_file" "kb_sync_upsert_zip" {
  type        = "zip"
  output_path = "${path.module}/../../dist/kb_sync_upsert_payload.zip"
  source {
    content  = file("${path.module}/../../app/lambdas/kb_sync/upsert/handler.py")
    filename = "handler.py"
  }
}

# 9. RDS INIT
data "archive_file" "rds_init_zip" {
  type        = "zip"
  output_path = "${path.module}/../../dist/rds_init_payload.zip"
  source {
    content  = file("${path.module}/../../app/lambdas/rds_init/handler.py")
    filename = "handler.py"
  }
}
