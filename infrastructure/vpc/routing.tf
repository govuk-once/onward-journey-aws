/**
 * PURPOSE: Shared network routing infrastructure (IGW, EIP, NAT Gateway, Route Tables).
 * Provides internet egress for public components and secure outbound egress for private subnets.
 */

# Internet Gateway for public subnet traffic
resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "shared-igw"
  }
}

# Dedicated EIP for the shared NAT Gateway
resource "aws_eip" "nat" {
  domain = "vpc"

  tags = {
    Name = "shared-nat-eip"
  }
}

# NAT Gateway placed in public DMZ subnet for private subnet outbound connectivity
resource "aws_nat_gateway" "nat" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public.id

  tags = {
    Name = "shared-nat-gateway"
  }

  depends_on = [aws_internet_gateway.igw]
}

# Public Route Table (0.0.0.0/0 -> IGW)
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }

  tags = {
    Name = "shared-public-rt"
  }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# Private Route Table (0.0.0.0/0 -> NAT Gateway)
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.nat.id
  }

  tags = {
    Name = "shared-private-rt"
  }
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# Fetch shared Slack notification topic provisioned by infrastructure/slack-alerts
data "aws_sns_topic" "slack_alerts" {
  name = "oj-aws-errors"
}

# Alarm for unexpected NAT Gateway bandwidth spikes during testing
resource "aws_cloudwatch_metric_alarm" "nat_bytes_processed_high" {
  alarm_name          = "shared-nat-bytes-processed-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "BytesProcessed"
  namespace           = "AWS/NATGateway"
  period              = 900 # 15-minute evaluation window
  statistic           = "Sum"

  # TODO: Increase threshold or extract to variable when promoting/replicating for UAT/Prod workloads
  threshold = 104857600 # 100 MB limit per 15 mins (~10,000 x 10KB payloads)

  dimensions = {
    NatGatewayId = aws_nat_gateway.nat.id
  }

  alarm_description = "Triggers if shared NAT Gateway bandwidth exceeds 100 MB within 15 minutes during dev/testing."
  alarm_actions     = [data.aws_sns_topic.slack_alerts.arn]
}
