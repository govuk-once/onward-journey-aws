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
resource "null_resource" "build_and_deploy_frontend" {
  triggers = {
    # Re-run deployment if the frontend source code changes
    frontend_hash = sha1(join("", [for f in fileset(abspath("${path.module}/../../frontend"), "{src,static}/**/*") : filemd5("${abspath("${path.module}/../../frontend")}/${f}")]))
    # Re-run if the backend URL changes
    backend_url = aws_lambda_function_url.orchestrator_url.function_url
    # Re-run if the Cognito pool changes
    cognito_pool_id = aws_cognito_identity_pool.frontend_anon.id
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e
      echo "Building frontend..."
      FRONTEND_DIR="${abspath("${path.module}/../../frontend")}"
      cd "$FRONTEND_DIR"

      # Install dependencies (using npm install instead of ci to update the lockfile with adapter-static)
      npm install

      # Build the app with the Orchestrator URL and Cognito pool injected
      export PUBLIC_ORCHESTRATOR_URL="${aws_lambda_function_url.orchestrator_url.function_url}"
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
