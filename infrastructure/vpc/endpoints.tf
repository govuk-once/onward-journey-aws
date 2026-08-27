/**
 * PURPOSE: Shared VPC Endpoints (Gateway and Interface Endpoints).
 * Enables private subnets to communicate with AWS services without traversing the public internet.
 */

data "aws_region" "current" {}

# --- S3 GATEWAY ENDPOINT ---
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${data.aws_region.current.id}.s3"
  vpc_endpoint_type = "Gateway"

  tags = {
    Name = "shared-s3-gateway"
  }
}

# Explicitly link S3 Gateway to Route Tables to avoid NAT data transfer fees
resource "aws_vpc_endpoint_route_table_association" "s3_main" {
  vpc_endpoint_id = aws_vpc_endpoint.s3.id
  route_table_id  = aws_vpc.main.main_route_table_id
}

resource "aws_vpc_endpoint_route_table_association" "s3_private" {
  vpc_endpoint_id = aws_vpc_endpoint.s3.id
  route_table_id  = aws_route_table.private.id
}

# --- SHARED INTERFACE ENDPOINTS ---

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

# CloudWatch Logs Endpoint - required for telemetry and error logging from private compute
resource "aws_vpc_endpoint" "logs" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${data.aws_region.current.id}.logs"
  vpc_endpoint_type = "Interface"

  # Dynamically fetches the IDs of the two private subnets
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.shared_endpoints_sg.id]
  private_dns_enabled = true

  tags = { Name = "shared-logs-endpoint" }
}

# STS Endpoint - required for boto3 and AgentCore Runtime credential resolution
resource "aws_vpc_endpoint" "sts" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${data.aws_region.current.id}.sts"
  vpc_endpoint_type = "Interface"

  # Dynamically fetches the IDs of the two private subnets
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.shared_endpoints_sg.id]
  private_dns_enabled = true

  tags = { Name = "shared-sts-endpoint" }
}
