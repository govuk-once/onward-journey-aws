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
# CRM WEBSOCKET ROUTER LAMBDA INTEGRATION (AWS_PROXY)
# -----------------------------------------------------------------------------
resource "aws_api_gateway_integration" "post_integration" {
  rest_api_id             = aws_api_gateway_rest_api.api.id
  resource_id             = local.target_resource_id
  http_method             = aws_api_gateway_method.post_method.http_method
  integration_http_method = "POST"
  type                    = "AWS_PROXY"
  uri                     = var.crm_wh_router_invoke_arn
}

resource "aws_lambda_permission" "crm_wh_router_invoke" {
  statement_id  = "AllowAPIGatewayInvokeRouter-${var.api_name}"
  action        = "lambda:InvokeFunction"
  function_name = var.crm_wh_router_function_name
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
      aws_api_gateway_authorizer.authorizer.identity_source
    ]))
  }

  lifecycle {
    create_before_destroy = true
  }

  depends_on = [
    aws_api_gateway_integration.post_integration
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
