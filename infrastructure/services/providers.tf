locals {
  # Known non-developer environments
  known_environments = {
    "production" = "production"
    "staging"    = "staging"
  }

  # Maps dev initials to canonical "development" for Environment tag as set out by:
  # https://gdsgovukagents.atlassian.net/wiki/spaces/TAG/pages/177995889/RFC+AWS+Resource+Tagging+Standards
  # Also see: https://gdsgovukagents.atlassian.net/wiki/spaces/TAG/pages/205291567/013+-+AWS+Resource+Tagging+Standards
  canonical_environment = lookup(local.known_environments, var.environment, "development")
}

terraform {
  required_version = "1.13.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.28.0" # min 6.28 required to enable use of invoked_via_function_url
    }

    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }

    null = {
      source  = "hashicorp/null"
      version = "~> 3.0"
    }

    local = {
      source  = "hashicorp/local"
      version = "~> 2.0"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.11"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }

    external = {
      source  = "hashicorp/external"
      version = "~> 2.0"
    }
  }

  # Configured per-environment in environments/<environment name>.config
  backend "s3" {
    # We leave bucket and key empty to be filled by .config files,
    # but we force the prefix structure here.
    workspace_key_prefix = "environment"
  }
}

provider "aws" {
  region = "eu-west-2"

  default_tags {
    tags = {
      Product           = "ai-govuk"
      Service           = "onward-journey"
      Component         = "backend-services"
      Environment       = local.canonical_environment
      Owner             = "onward-journey-team"
      Source            = "onward-journey-aws"
      PipelineStackName = var.environment # Preserves specific developer workspace (dev initials)
    }
  }
}
