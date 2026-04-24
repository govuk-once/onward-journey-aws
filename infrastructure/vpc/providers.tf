terraform {
  required_version = "1.13.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.28.0"
    }
  }

  backend "s3" {
    # We do not have a workspace_key_prefix because the VPC uses the 'default' workspace.
    # Specify a dedicated static key for the shared network state.
    # (Bucket and region can still be passed in via your .config file during init)
    key = "shared-infrastructure/vpc.tfstate"
  }
}

provider "aws" {
  region = "eu-west-2"

  default_tags {
    tags = {
      Project = "GOV.UK Agents Onward Journey"
      # Hardcoded! The network belongs to everyone, not a specific developer.
      Environment = "shared"
    }
  }
}
