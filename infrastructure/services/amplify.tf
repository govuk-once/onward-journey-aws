locals {
  # 10 branches on 'ux', 2 on any other environment
  tester_count   = var.environment == "ux" ? 10 : 2
  tester_indices = toset([for i in range(1, local.tester_count + 1) : tostring(i)])
}

# 1. Random suffix for branch names to prevent predictable URLs in code
resource "random_id" "tester_branch_suffix" {
  for_each    = local.tester_indices
  byte_length = 3 # Generates 6 hex characters, e.g. "a3f9c2"
}

# 2. Usernames for Basic Auth
resource "random_pet" "tester_usernames" {
  for_each  = local.tester_indices
  length    = 3
  separator = "-"
}

# 3. Random passwords for Amplify Basic Auth
resource "random_password" "tester_passwords" {
  for_each = local.tester_indices
  length   = 10
  special  = false
  upper    = false
}

# 4. UUIDs for WebSocket handshake tokens
resource "random_uuid" "ws_tokens" {
  for_each = local.tester_indices
}

# 5. UUIDs for Bedrock AgentCore & DynamoDB session memory
resource "random_uuid" "actor_ids" {
  for_each = local.tester_indices
}

# -----------------------------------------------------------------------------
# Amplify Frontend
# -----------------------------------------------------------------------------

# 1. Create the Amplify App
resource "aws_amplify_app" "frontend" {
  name = "${var.environment}-onward-journey-frontend"
  # Note: No repository configuration. Deployments are pushed manually via null_resource.
}

# 2. Create the Multi-Tester Branches
resource "aws_amplify_branch" "testers" {
  for_each = local.tester_indices

  app_id      = aws_amplify_app.frontend.id
  branch_name = "tester-${each.key}-${random_id.tester_branch_suffix[each.key].hex}"

  enable_basic_auth      = true
  basic_auth_credentials = base64encode("${random_pet.tester_usernames[each.key].id}:${random_password.tester_passwords[each.key].result}")

  lifecycle {
    ignore_changes = [basic_auth_credentials]
  }
}

# 3. Build and Deploy Script
locals {
  frontend_dir = abspath("${path.module}/../../frontend")

  # Hash only hand-authored source files.
  frontend_src_hash = sha1(join("", [
    for f in fileset(local.frontend_dir, "src/**/*") : filemd5("${local.frontend_dir}/${f}")
  ]))

  # Hash hand-authored static files (robots.txt etc.) but not the auto-generated assets/ subdir
  frontend_static_hash = sha1(join("", [
    for f in fileset(local.frontend_dir, "static/*") : filemd5("${local.frontend_dir}/${f}")
  ]))

  # Separately hash dependency manifests and build-affecting config files
  frontend_config_hash = sha1(join("", [
    filemd5("${local.frontend_dir}/package.json"),
    filemd5("${local.frontend_dir}/package-lock.json"),
    filemd5("${local.frontend_dir}/vite.config.ts"),
  ]))
}

resource "null_resource" "build_and_deploy_frontend" {
  triggers = {
    # 1. Trigger rebuild if the number of testers changes (e.g., dev vs ux workspace)
    tester_count = local.tester_count

    # 2. Trigger rebuild if any frontend files or backend config changes
    frontend_src_hash    = local.frontend_src_hash
    frontend_static_hash = local.frontend_static_hash
    frontend_config_hash = local.frontend_config_hash
    backend_url          = module.client_ws_gateway.wss_url

    # 3. Trigger rebuild if the generated tokens change
    tokens_hash = sha256(jsonencode({
      for idx in local.tester_indices :
      random_uuid.ws_tokens[idx].result => random_uuid.actor_ids[idx].result
    }))
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e

      export AWS_DEFAULT_REGION="${var.aws_region}"

      FRONTEND_DIR="${local.frontend_dir}"
      cd "$FRONTEND_DIR"

      npm ci

      SECRET_JSON=$(aws secretsmanager get-secret-value \
        --secret-id "${aws_secretsmanager_secret.ws_auth_tokens.id}" \
        --query SecretString \
        --output text)

      %{for idx in local.tester_indices~}

      BRANCH_NAME="${aws_amplify_branch.testers[idx].branch_name}"
      echo "=== Building bundle for branch: $BRANCH_NAME ==="

      # Force cache clearing to prevent Vite/SvelteKit contamination
      rm -rf .svelte-kit build

      # Extract the token dynamically using Python to prevent Terraform leakage
      export PUBLIC_WEBSOCKET_AUTH_TOKEN=$(echo "$SECRET_JSON" | python3 -c "import sys, json; print(list(json.load(sys.stdin).keys())[${idx} - 1])")

      export PUBLIC_CLIENT_WEBSOCKET_URL="${module.client_ws_gateway.wss_url}"

      npm run build

      cd build
      zip -q -r "../frontend_${idx}.zip" ./*
      cd ..

      # Deploy to Amplify
      RES=$(aws amplify create-deployment \
        --app-id "${aws_amplify_app.frontend.id}" \
        --branch-name "$BRANCH_NAME" \
        --output json)

      JOB_ID=$(echo "$RES" | python3 -c "import sys, json; print(json.load(sys.stdin)['jobId'])")
      UPLOAD_URL=$(echo "$RES" | python3 -c "import sys, json; print(json.load(sys.stdin)['zipUploadUrl'])")

      curl -s -T "frontend_${idx}.zip" "$UPLOAD_URL"

      aws amplify start-deployment \
        --app-id "${aws_amplify_app.frontend.id}" \
        --branch-name "$BRANCH_NAME" \
        --job-id "$JOB_ID"

      rm "frontend_${idx}.zip"

      %{endfor~}

      echo "All tester branches built and deployed successfully."
    EOT
  }

  depends_on = [
    aws_amplify_branch.testers,
    aws_secretsmanager_secret_version.ws_auth_tokens_val
  ]
}

output "amplify_app_id" {
  value = aws_amplify_app.frontend.id
}

# -----------------------------------------------------------------------------
# WAF & Edge Protections
# -----------------------------------------------------------------------------

# Note: region acts as the "control plane" for global services.
# The data stored in the US is purely configuration metadata
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

resource "aws_wafv2_web_acl" "amplify_uk_only" {
  provider    = aws.us_east_1
  name        = "${var.environment}-amplify-uk-only"
  description = "Block all non-UK traffic to the Amplify frontend"
  scope       = "CLOUDFRONT" # Amplify requires the global CLOUDFRONT scope

  default_action {
    block {} # Block everything by default
  }

  rule {
    name     = "allow-uk-traffic"
    priority = 1

    action {
      allow {}
    }

    statement {
      geo_match_statement {
        country_codes = ["GB"]
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "allow-uk-traffic-metric"
      sampled_requests_enabled   = false
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.environment}-amplify-uk-only-metric"
    sampled_requests_enabled   = false
  }
}

resource "aws_wafv2_web_acl_association" "amplify_waf" {
  provider     = aws.us_east_1
  resource_arn = aws_amplify_app.frontend.arn
  web_acl_arn  = aws_wafv2_web_acl.amplify_uk_only.arn
}

# -----------------------------------------------------------------------------
# Tester Credentials in Secrets Manager
# -----------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "tester_credentials" {
  name        = "${var.environment}/frontend/tester-credentials"
  description = "Basic Auth credentials for frontend user testing"
}

resource "aws_secretsmanager_secret_version" "tester_credentials_val" {
  secret_id = aws_secretsmanager_secret.tester_credentials.id

  secret_string = jsonencode({
    for idx in local.tester_indices : "Tester ${idx}" => {
      username = random_pet.tester_usernames[idx].id
      password = random_password.tester_passwords[idx].result
    }
  })
}
