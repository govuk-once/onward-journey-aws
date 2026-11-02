/**
 * PURPOSE: Core VPC and subnet definitions for the Onward Journey Shared Network.
 * Deployed once via the 'default' workspace as a persistent shell across developer environments.
 */

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  lifecycle {
    # Permanent network shell. To destroy, set prevent_destroy = false manually.
    prevent_destroy = true
  }

  tags = {
    Name = "main-vpc"
  }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.${count.index + 1}.0/24"
  availability_zone = "eu-west-2${count.index == 0 ? "a" : "b"}"

  lifecycle {
    # Guardrail: Prevents accidental loss of RDS or compute subnets.
    prevent_destroy = true
  }

  tags = {
    Name = "app-pvt-2${count.index == 0 ? "a" : "b"}"
    Tier = "app-private" # Targeted by services/network_data.tf for Lambda/RDS placement
  }
}

resource "aws_subnet" "public" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.100.0/24"
  availability_zone = "eu-west-2a"

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Name = "dmz-pub-2a"
    Tier = "dmz-public" # DMZ subnet reserved for public ingress and NAT Gateway
  }
}
