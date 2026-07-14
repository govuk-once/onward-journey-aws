terraform {
  required_version = "1.13.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.28.0"
    }
  }

  backend "s3" {
    # backend configuration passed in dynamically via slack-alerts.config
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
