## SHARED LAMBDA LAYER BUILD
# Consolidates all common dependencies and shared logic into separate Core and Integrations layers.

locals {
  # 1. Absolute paths
  app_dir                  = abspath("${path.module}/../../app")
  core_staging_dir         = abspath("${path.module}/../../dist/core_layer")
  integrations_staging_dir = abspath("${path.module}/../../dist/integrations_layer")
  core_output_zip          = abspath("${path.module}/../../dist/core_layer_payload.zip")
  integrations_output_zip  = abspath("${path.module}/../../dist/integrations_layer_payload.zip")

  # 2. Track source code changes
  core_layer_triggers = {
    lock_file    = filemd5("${local.app_dir}/uv.lock")
    shared_utils = sha1(join("", [for f in fileset("${local.app_dir}/shared/utils/", "**/*.py") : filemd5("${local.app_dir}/shared/utils/${f}")]))
  }

  integrations_layer_triggers = {
    integrations = sha1(join("", [for f in fileset("${local.app_dir}/shared/integrations/", "**/*.py") : filemd5("${local.app_dir}/shared/integrations/${f}")]))
  }

  # Generate unique IDs for each layer
  core_trigger_hash         = sha1(jsonencode(local.core_layer_triggers))
  integrations_trigger_hash = sha1(jsonencode(local.integrations_layer_triggers))

  # 3. Build scripts
  core_build_command = <<EOT
    set -e
    echo "Starting Core Lambda Layer build..."

    STAGING_DIR="${local.core_staging_dir}"
    OUTPUT_ZIP="${local.core_output_zip}"
    APP_DIR="${local.app_dir}"

    # 1. Prepare staging directory
    rm -rf "$STAGING_DIR"
    rm -f "$OUTPUT_ZIP"
    mkdir -p "$STAGING_DIR/python"

    # 2. INSTALL DEPENDENCIES
    echo "Installing external dependencies from pyproject.toml..."
    cd "$APP_DIR"

    # We use uv to install dependencies into the layer's python directory.
    # We explicitly target the Lambda's runtime platform (Amazon Linux 2023 / manylinux_2_28).
    # Since we use absolute paths for --target, we never lose the folder!
    uv pip install \
      --target "$STAGING_DIR/python" \
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

    # 4. COPY UTILS
    echo "Copying shared internal utilities..."
    mkdir -p "$STAGING_DIR/python/utils"
    cp "$APP_DIR/shared/utils/"*.py "$STAGING_DIR/python/utils/"

    # 5. ZIP
    echo "Zipping core lambda layer..."
    cd "$STAGING_DIR"
    zip -r "$OUTPUT_ZIP" python > /dev/null

    echo "Core Lambda Layer build complete."
    EOT

  integrations_build_command = <<EOT
    set -e
    echo "Starting Integrations Lambda Layer build..."

    STAGING_DIR="${local.integrations_staging_dir}"
    OUTPUT_ZIP="${local.integrations_output_zip}"
    APP_DIR="${local.app_dir}"

    # 1. Prepare staging directory
    rm -rf "$STAGING_DIR"
    rm -f "$OUTPUT_ZIP"
    mkdir -p "$STAGING_DIR/python"

    # 2. COPY INTEGRATIONS
    echo "Copying shared integrations..."
    mkdir -p "$STAGING_DIR/python/integrations/providers"
    cp "$APP_DIR/shared/integrations/"*.py "$STAGING_DIR/python/integrations/"
    cp "$APP_DIR/shared/integrations/providers/"*.py "$STAGING_DIR/python/integrations/providers/"

    # 3. ZIP
    echo "Zipping integrations lambda layer..."
    cd "$STAGING_DIR"
    zip -r "$OUTPUT_ZIP" python > /dev/null

    echo "Integrations Lambda Layer build complete."
    EOT
}

resource "null_resource" "build_core_layer" {
  triggers = {
    build_hash   = local.core_trigger_hash
    build_script = sha1(local.core_build_command)
  }

  provisioner "local-exec" {
    command = local.core_build_command
  }
}

resource "null_resource" "build_integrations_layer" {
  triggers = {
    build_hash   = local.integrations_trigger_hash
    build_script = sha1(local.integrations_build_command)
  }

  provisioner "local-exec" {
    command = local.integrations_build_command
  }
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

# 9. KB SYNC: UPDATE SYNC META
data "archive_file" "kb_sync_update_sync_meta_zip" {
  type        = "zip"
  output_path = "${path.module}/../../dist/kb_sync_update_sync_meta_payload.zip"
  source {
    content  = file("${path.module}/../../app/lambdas/kb_sync/update_sync_meta/handler.py")
    filename = "handler.py"
  }
}

# 10. RDS INIT
data "archive_file" "rds_init_zip" {
  type        = "zip"
  output_path = "${path.module}/../../dist/rds_init_payload.zip"
  source {
    content  = file("${path.module}/../../app/lambdas/rds_init/handler.py")
    filename = "handler.py"
  }
}
