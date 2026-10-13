variable "frontend_basic_auth_username" {
  type        = string
  description = "Username for basic auth on the frontend"
}

variable "frontend_basic_auth_password" {
  type        = string
  description = "Password for basic auth on the frontend"
  sensitive   = true
}

# 1. Create the Amplify App
resource "aws_amplify_app" "frontend" {
  name = "${var.environment}-onward-journey-frontend"

  # Note: No repository configuration. Deployments are pushed manually via null_resource.
}

# 2. Create the Branch
resource "aws_amplify_branch" "main" {
  app_id      = aws_amplify_app.frontend.id
  branch_name = "main"

  enable_basic_auth      = true
  basic_auth_credentials = base64encode("${var.frontend_basic_auth_username}:${var.frontend_basic_auth_password}")
}

# 3. Build and Deploy Script
locals {
  frontend_dir = abspath("${path.module}/../../frontend")

  # Hash only hand-authored source files.
  # Excludes build artifacts (build/, .svelte-kit/, node_modules/, frontend_payload.zip)
  # AND static/assets/ which is auto-populated from node_modules by the `prepare` script
  # (make prepare copies govuk-frontend assets there on every npm install).
  # govuk-frontend version changes are captured via package-lock.json in frontend_config_hash.
  frontend_src_hash = sha1(join("", [
    for f in fileset(local.frontend_dir, "src/**/*") :
    filemd5("${local.frontend_dir}/${f}")
  ]))

  # Hash hand-authored static files (robots.txt etc.) but not the auto-generated assets/ subdir
  frontend_static_hash = sha1(join("", [
    for f in fileset(local.frontend_dir, "static/*") :
    filemd5("${local.frontend_dir}/${f}")
  ]))

  # Separately hash dependency manifests and build-affecting config files
  # so that dependency or tooling changes also trigger a redeploy.
  frontend_config_hash = sha1(join("", [
    filemd5("${local.frontend_dir}/package.json"),
    filemd5("${local.frontend_dir}/package-lock.json"),
    filemd5("${local.frontend_dir}/svelte.config.js"),
    filemd5("${local.frontend_dir}/vite.config.ts"),
  ]))
}

resource "null_resource" "build_and_deploy_frontend" {
  triggers = {
    # Re-run if frontend source files (src/) change
    frontend_src_hash = local.frontend_src_hash
    # Re-run if hand-authored static files change (excludes auto-copied static/assets/)
    frontend_static_hash = local.frontend_static_hash
    # Re-run if dependencies or build tooling config change
    frontend_config_hash = local.frontend_config_hash
    # Re-run if the WebSocket URL changes
    backend_url = module.client_ws_gateway.wss_url
    # Re-run if the Cognito pool changes
    cognito_pool_id = aws_cognito_identity_pool.frontend_anon.id
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e
      echo "Building frontend..."
      FRONTEND_DIR="${local.frontend_dir}"
      cd "$FRONTEND_DIR"

      # Use 'npm ci' to install from the lockfile without modifying it
      npm ci

      # Build the app with the Orchestrator URL and Cognito pool injected
      export PUBLIC_ORCHESTRATOR_URL="${module.client_ws_gateway.wss_url}"
      export PUBLIC_COGNITO_IDENTITY_POOL_ID="${aws_cognito_identity_pool.frontend_anon.id}"
      export PUBLIC_AWS_REGION="${var.aws_region}"
      npm run build

      # Zip the output
      cd build
      zip -r ../frontend_payload.zip ./* > /dev/null
      cd ..

      # Deploy to Amplify
      echo "Deploying to Amplify..."
      APP_ID="${aws_amplify_app.frontend.id}"
      BRANCH="main"

      # Create deployment and extract URLs using python to parse JSON
      RES=$(aws amplify create-deployment --app-id $APP_ID --branch-name $BRANCH --output json)
      JOB_ID=$(echo "$RES" | python3 -c "import sys, json; print(json.load(sys.stdin)['jobId'])")
      UPLOAD_URL=$(echo "$RES" | python3 -c "import sys, json; print(json.load(sys.stdin)['zipUploadUrl'])")

      # Upload zip securely (using quotes around the URL)
      curl -s -T frontend_payload.zip "$UPLOAD_URL"

      # Start deployment
      aws amplify start-deployment --app-id $APP_ID --branch-name $BRANCH --job-id $JOB_ID

      echo "Frontend deployment triggered successfully!"
    EOT
  }

  depends_on = [
    aws_amplify_branch.main
  ]
}

output "amplify_app_id" {
  value = aws_amplify_app.frontend.id
}

output "amplify_main_branch_url" {
  value = "https://main.${aws_amplify_app.frontend.id}.amplifyapp.com"
}
