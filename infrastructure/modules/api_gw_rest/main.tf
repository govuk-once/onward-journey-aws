# -----------------------------------------------------------------------------
# REST API GATEWAY
# -----------------------------------------------------------------------------
resource "aws_api_gateway_rest_api" "api" {
  name        = "${var.environment}-${var.api_name}-gw"
  description = var.api_description

  endpoint_configuration {
    types = ["REGIONAL"]
  }
}

# -----------------------------------------------------------------------------
# DYNAMIC RESOURCE PATH (Support up to 2 path levels, e.g., /contentguru/webhook)
# -----------------------------------------------------------------------------
resource "aws_api_gateway_resource" "level1" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_rest_api.api.root_resource_id
  path_part   = var.path_parts[0]
}

resource "aws_api_gateway_resource" "level2" {
  count       = length(var.path_parts) > 1 ? 1 : 0
  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_resource.level1.id
  path_part   = var.path_parts[1]
}

locals {
  target_resource_id = length(var.path_parts) > 1 ? aws_api_gateway_resource.level2[0].id : aws_api_gateway_resource.level1.id
}

resource "aws_api_gateway_method" "post_method" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  resource_id   = local.target_resource_id
  http_method   = "POST"
  authorization = "CUSTOM"
  authorizer_id = aws_api_gateway_authorizer.authorizer.id
}

# -----------------------------------------------------------------------------
# BACKEND PROCESSOR LAMBDA INTEGRATION (AWS_PROXY)
# -----------------------------------------------------------------------------
# TODO(JOUR-346): Revert type to "AWS_PROXY", restore uri = var.processor_lambda_invoke_arn, and remove request_templates once processor Lambda exists
resource "aws_api_gateway_integration" "post_integration" {
  rest_api_id             = aws_api_gateway_rest_api.api.id
  resource_id             = local.target_resource_id
  http_method             = aws_api_gateway_method.post_method.http_method
  integration_http_method = "POST"
  type                    = var.processor_lambda_invoke_arn != "" ? "AWS_PROXY" : "MOCK"
  uri                     = var.processor_lambda_invoke_arn != "" ? var.processor_lambda_invoke_arn : null

  request_templates = var.processor_lambda_invoke_arn == "" ? {
    "application/json" = "{\"statusCode\": 200}"
  } : null
}

# TODO(JOUR-346): Delete mock method response once processor Lambda is integrated via AWS_PROXY
resource "aws_api_gateway_method_response" "response_200" {
  count       = var.processor_lambda_invoke_arn == "" ? 1 : 0
  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = local.target_resource_id
  http_method = aws_api_gateway_method.post_method.http_method
  status_code = "200"
}

# TODO(JOUR-346): Delete mock integration response once processor Lambda is integrated via AWS_PROXY
resource "aws_api_gateway_integration_response" "integration_response_200" {
  count       = var.processor_lambda_invoke_arn == "" ? 1 : 0
  rest_api_id = aws_api_gateway_rest_api.api.id
  resource_id = local.target_resource_id
  http_method = aws_api_gateway_method.post_method.http_method
  status_code = aws_api_gateway_method_response.response_200[0].status_code

  response_templates = {
    "application/json" = "{\"message\": \"Authorizer verification successful (Mock Backend)\"}"
  }

  depends_on = [aws_api_gateway_integration.post_integration]
}

# TODO(JOUR-346): Remove count guard once downstream processor Lambda function is provisioned
resource "aws_lambda_permission" "processor_invoke" {
  count         = var.processor_lambda_function_name != "" ? 1 : 0
  statement_id  = "AllowAPIGatewayInvokeProcessor-${var.api_name}"
  action        = "lambda:InvokeFunction"
  function_name = var.processor_lambda_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.api.execution_arn}/*/*"
}

# -----------------------------------------------------------------------------
# CUSTOM LAMBDA AUTHORIZER
# -----------------------------------------------------------------------------
resource "aws_api_gateway_authorizer" "authorizer" {
  name                             = "${var.environment}-${var.api_name}-authorizer"
  rest_api_id                      = aws_api_gateway_rest_api.api.id
  authorizer_uri                   = var.authorizer_lambda_invoke_arn
  authorizer_credentials           = ""
  identity_source                  = "method.request.header.X-Storm-Signature"
  type                             = "REQUEST"
  authorizer_result_ttl_in_seconds = 0
}

resource "aws_lambda_permission" "authorizer_invoke" {
  statement_id  = "AllowAPIGatewayInvokeAuthorizer-${var.api_name}"
  action        = "lambda:InvokeFunction"
  function_name = var.authorizer_lambda_function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_api_gateway_rest_api.api.execution_arn}/*/*"
}

# -----------------------------------------------------------------------------
# DEPLOYMENT & STAGE
# -----------------------------------------------------------------------------
resource "aws_api_gateway_deployment" "deployment" {
  rest_api_id = aws_api_gateway_rest_api.api.id

  triggers = {
    redeployment = sha1(jsonencode([
      aws_api_gateway_resource.level1.id,
      # Level 1 Path
      aws_api_gateway_resource.level1.id,
      aws_api_gateway_resource.level1.path_part,

      # Level 2 Path (Optional)
      length(aws_api_gateway_resource.level2) > 0 ? aws_api_gateway_resource.level2[0].id : "",
      length(aws_api_gateway_resource.level2) > 0 ? aws_api_gateway_resource.level2[0].path_part : "",

      # Method & Integration
      aws_api_gateway_method.post_method.id,
      aws_api_gateway_method.post_method.authorization,
      aws_api_gateway_method.post_method.authorizer_id,
      aws_api_gateway_integration.post_integration.id,
      aws_api_gateway_integration.post_integration.uri,
      aws_api_gateway_integration.post_integration.type,

      # Authorizer
      aws_api_gateway_authorizer.authorizer.id,
      aws_api_gateway_authorizer.authorizer.authorizer_uri,
      aws_api_gateway_authorizer.authorizer.authorizer_result_ttl_in_seconds,
      aws_api_gateway_authorizer.authorizer.identity_source,

      # Responses
      aws_api_gateway_method_response.response_200.id,
      aws_api_gateway_integration_response.integration_response_200.id
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }

  # TODO(JOUR-346): Remove aws_api_gateway_integration_response.integration_response_200 from depends_on once mock integration is removed
  depends_on = [
    aws_api_gateway_integration.post_integration,
    aws_api_gateway_integration_response.integration_response_200
  ]
}

resource "aws_api_gateway_stage" "stage" {
  deployment_id = aws_api_gateway_deployment.deployment.id
  rest_api_id   = aws_api_gateway_rest_api.api.id
  stage_name    = var.environment
}

# -----------------------------------------------------------------------------
# WAF & RATE LIMITING
# -----------------------------------------------------------------------------
resource "aws_wafv2_web_acl" "waf" {
  name        = "${var.environment}-${var.api_name}-waf"
  description = "WAF for ${var.api_name} API Gateway"
  scope       = "REGIONAL"

  default_action {
    allow {}
  }

  rule {
    name     = "RateLimitRule"
    priority = 1

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit                 = var.waf_rate_limit
        aggregate_key_type    = "IP"
        evaluation_window_sec = var.waf_evaluation_window_sec
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.environment}-${var.api_name}-RateLimit"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.environment}-${var.api_name}-WAF"
    sampled_requests_enabled   = true
  }
}

resource "aws_wafv2_web_acl_association" "waf_assoc" {
  resource_arn = aws_api_gateway_stage.stage.arn
  web_acl_arn  = aws_wafv2_web_acl.waf.arn
}
