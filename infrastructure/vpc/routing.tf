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

# -----------------------------------------------------------------------------
# NAT Gateway Security & Performance CloudWatch Alarms
# -----------------------------------------------------------------------------

# Outbound Egress Alarm (Data Exfiltration / Large Payload Safeguard)
resource "aws_cloudwatch_metric_alarm" "nat_outbound_bytes_high" {
  alarm_name          = "shared-nat-outbound-bytes-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "BytesOutToDestination"
  namespace           = "AWS/NATGateway"
  period              = 300 # 5-minute evaluation window for fast feedback in dev
  statistic           = "Sum"
  threshold           = 104857600 # 100 MB per 5 mins

  dimensions = {
    NatGatewayId = aws_nat_gateway.nat.id
  }

  alarm_description = "Alerts on excessive outbound internet transfer from private subnets (e.g. data exfiltration or runaway API request payloads)."
  alarm_actions     = [data.aws_sns_topic.slack_alerts.arn]
}

# Inbound Response Alarm (Runaway Download Loops / Large Payload Safeguard)
resource "aws_cloudwatch_metric_alarm" "nat_inbound_bytes_high" {
  alarm_name          = "shared-nat-inbound-bytes-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "BytesInFromDestination"
  namespace           = "AWS/NATGateway"
  period              = 300
  statistic           = "Sum"
  threshold           = 104857600 # 100 MB per 5 mins

  dimensions = {
    NatGatewayId = aws_nat_gateway.nat.id
  }

  alarm_description = "Alerts on high inbound internet traffic returned to private subnets (e.g. runaway response loops or heavy payloads)."
  alarm_actions     = [data.aws_sns_topic.slack_alerts.arn]
}

# Connection Attempt Spike Alarm (Rogue AI Loops & Infinite Retries)
resource "aws_cloudwatch_metric_alarm" "nat_connection_attempts_high" {
  alarm_name          = "shared-nat-connection-attempts-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ConnectionAttemptCount"
  namespace           = "AWS/NATGateway"
  period              = 300
  statistic           = "Sum"
  threshold           = 1000 # Max 1,000 connection attempts per 5 mins

  dimensions = {
    NatGatewayId = aws_nat_gateway.nat.id
  }

  alarm_description = "Alerts on rapid connection spikes, flagging unthrottled API retry storms or recursive LangGraph loops."
  alarm_actions     = [data.aws_sns_topic.slack_alerts.arn]
}

# Port Allocation Failure Alarm (Network Exhaustion / Socket Leak Safeguard)
resource "aws_cloudwatch_metric_alarm" "nat_port_allocation_errors" {
  alarm_name          = "shared-nat-port-allocation-errors"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ErrorPortAllocation"
  namespace           = "AWS/NATGateway"
  period              = 60 # 1-minute window for immediate availability alerts
  statistic           = "Sum"
  threshold           = 0

  dimensions = {
    NatGatewayId = aws_nat_gateway.nat.id
  }

  alarm_description = "Alerts immediately if the NAT Gateway cannot allocate a source port due to connection limit exhaustion."
  alarm_actions     = [data.aws_sns_topic.slack_alerts.arn]
}
