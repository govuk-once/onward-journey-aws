/**
 * PURPOSE: Foundations for the Onward Journey Shared Network.
 * This is deployed ONCE via the 'default' workspace.
 * It acts as the permanent shell for all developer environments.
 */

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  lifecycle {
    # Foundations are permanent. To delete, set to false.
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
    Tier = "app-private" # # Used by services/network_data.tf to identify targets for Lambda/RDS.
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
    Tier = "dmz-public" # Reserved for NAT Gateway and public-facing ingress components.
  }
}

# --- S3 GATEWAY ---
# Required for private subnets to reach S3 buckets
# 1. Fetch current region
data "aws_region" "current" {}

# 2. S3 Gateway Endpoint
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${data.aws_region.current.id}.s3"
  vpc_endpoint_type = "Gateway"

  tags = {
    Name = "shared-s3-gateway"
  }
}

# 3. Explicitly link the S3 Gateway to the Main Route Table
resource "aws_vpc_endpoint_route_table_association" "s3_main" {
  vpc_endpoint_id = aws_vpc_endpoint.s3.id
  route_table_id  = aws_vpc.main.main_route_table_id
}
# --- SHARED INTERFACE ENDPOINTS ---
# 4. Security Group for Shared Endpoints
resource "aws_security_group" "shared_endpoints_sg" {
  name        = "shared-endpoints-sg"
  description = "Allow HTTPS traffic from within the VPC to shared endpoints"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.main.cidr_block]
  }
}

# 5. CloudWatch Logs Endpoint - required for AgentCore Runtime telemetry and error logging
resource "aws_vpc_endpoint" "logs" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${data.aws_region.current.id}.logs"
  vpc_endpoint_type = "Interface"

  # Dynamically fetches the IDs of the two private subnets created at the top of this file
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.shared_endpoints_sg.id]
  private_dns_enabled = true

  tags = { Name = "shared-logs-endpoint" }
}

# 6. STS Endpoint - required for Boto3 and AgentCore Runtime to fetch IAM credentials
resource "aws_vpc_endpoint" "sts" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${data.aws_region.current.id}.sts"
  vpc_endpoint_type = "Interface"

  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.shared_endpoints_sg.id]
  private_dns_enabled = true

  tags = { Name = "shared-sts-endpoint" }
}
