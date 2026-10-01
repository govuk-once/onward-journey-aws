/**
 * PURPOSE: Shared network routing infrastructure (IGW, EIP, NAT Gateway, Route Tables).
 * Provides internet egress for public components and secure outbound egress for private subnets.
 */

# Internet Gateway for public subnet traffic
resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.main.id

  tags = { Name = "shared-igw" }
}

# Dedicated EIP for the shared NAT Gateway
resource "aws_eip" "nat" {
  domain = "vpc"

  tags = { Name = "shared-nat-eip" }
}

# NAT Gateway placed in public DMZ subnet for private subnet outbound connectivity
resource "aws_nat_gateway" "nat" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public.id

  tags = { Name = "shared-nat-gateway" }

  depends_on = [aws_internet_gateway.igw]
}

# Public Route Table (0.0.0.0/0 -> IGW)
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }

  tags = { Name = "shared-public-rt" }
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

  tags = { Name = "shared-private-rt" }
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

locals {
  nat_alarms = {
    # Outbound Egress Alarm (Data Exfiltration / Large Payload Safeguard)
    outbound_bytes_high = {
      alarm_name        = "shared-nat-outbound-bytes-high"
      metric_name       = "BytesOutToDestination"
      period            = 300       # 5-minute evaluation window for fast feedback
      threshold         = 104857600 # 100 MB per 5 mins
      alarm_description = "Alerts on excessive outbound internet transfer from private subnets (e.g. data exfiltration or runaway API request payloads)."
    }

    # Inbound Response Alarm (Runaway Download Loops / Large Payload Safeguard)
    inbound_bytes_high = {
      alarm_name        = "shared-nat-inbound-bytes-high"
      metric_name       = "BytesInFromDestination"
      period            = 300
      threshold         = 104857600 # 100 MB per 5 mins
      alarm_description = "Alerts on high inbound internet traffic returned to private subnets (e.g. runaway response loops or heavy payloads)."
    }

    # Connection Attempt Spike Alarm (Rogue AI Loops & Infinite Retries)
    connection_attempts_high = {
      alarm_name        = "shared-nat-connection-attempts-high"
      metric_name       = "ConnectionAttemptCount"
      period            = 300
      threshold         = 1000 # Max 1,000 connection attempts per 5 mins
      alarm_description = "Alerts on rapid connection spikes, flagging unthrottled API retry storms or recursive LangGraph loops."
    }

    # Port Allocation Failure Alarm (Network Exhaustion / Socket Leak Safeguard)
    port_allocation_errors = {
      alarm_name        = "shared-nat-port-allocation-errors"
      metric_name       = "ErrorPortAllocation"
      period            = 60 # 1-minute window for immediate availability alerts
      threshold         = 0
      alarm_description = "Alerts immediately if the NAT Gateway cannot allocate a source port due to connection limit exhaustion."
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "nat_alarms" {
  for_each = local.nat_alarms

  alarm_name          = each.value.alarm_name
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = each.value.metric_name
  namespace           = "AWS/NATGateway"
  period              = each.value.period
  statistic           = "Sum"
  threshold           = each.value.threshold

  dimensions = {
    NatGatewayId = aws_nat_gateway.nat.id
  }

  alarm_description = each.value.alarm_description
  alarm_actions     = [data.aws_sns_topic.slack_alerts.arn]

  tags = { Name = each.value.alarm_name }
}
