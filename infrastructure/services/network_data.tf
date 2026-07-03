/**
 * PURPOSE: Foundational Network Discovery.
 * This file bridges the gap between the permanent shared network and this
 * ephemeral developer workspace. It looks up the core network resources deployed
 * by the 'infrastructure/vpc' component so that developer infrastructure
 * (Lambdas, Endpoints, Security Groups) can be securely placed inside them.
 * * DEPENDENCY: The 'infrastructure/vpc' Terraform must be applied before this workspace.
 */

# 1. Identify the shared foundational VPC
data "aws_vpc" "shared" {
  filter {
    name   = "tag:Name"
    values = ["main-vpc"]
  }
}

# 2. Discover the isolated application subnets intended for compute and data workloads
data "aws_subnets" "private" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.shared.id]
  }
  filter {
    name   = "tag:Tier"
    values = ["app-private"]
  }
}

# 3. Standardise the network outputs for use across the developer workspace
locals {
  vpc_id             = data.aws_vpc.shared.id
  private_subnet_ids = data.aws_subnets.private.ids
}
