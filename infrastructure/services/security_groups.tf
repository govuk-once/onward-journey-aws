/**
 * PURPOSE: Network Security and Traffic Control.
 * This file defines the firewall rules for the Orchestration Layer and its
 * private connectivity to AWS services. All rules should follow the principle of
 * least privilege, restricting traffic to specific security group IDs.
 */

# ============= SECURITY GROUPS ===================================================
# ORCHESTRATOR SECURITY GROUP
# Controls traffic for the Lambda-based logic layer.
resource "aws_security_group" "orchestrator" {
  name        = "${var.environment}-orchestrator-sg"
  description = "Security group for the Orchestration Layer for environment: ${var.environment}"
  vpc_id      = local.vpc_id

  tags = {
    Name = "${var.environment}-orchestrator-sg"
  }
}

# VPC ENDPOINTS SECURITY GROUP
# Provides a secure ingress point for the Orchestrator and RDS Seeder to reach all AWS Services.
resource "aws_security_group" "vpc_endpoints" {
  name        = "${var.environment}-vpc-endpoints-sg"
  description = "Private interface for the Orchestrator to reach Bedrock and Secrets Manager"
  vpc_id      = local.vpc_id

  tags = {
    Name = "${var.environment}-vpc-endpoints-sg"
  }
}

# SECRETS MANAGER SECURITY GROUP
# Provides a secure ingress point for RDS Init to access the secrets manager VPC endpoint only
resource "aws_security_group" "secrets_manager" {
  name        = "${var.environment}-secrets-manger-sg"
  description = "Private interface for RDS Init to reach Secrets Manager"
  vpc_id      = local.vpc_id

  tags = {
    Name = "${var.environment}-secrets-manager-sg"
  }
}

# BEDROCK SECURITY GROUP
# Provides a secure ingress point for RDS Tool to access the Bedrock VPC endpoint only
resource "aws_security_group" "bedrock" {
  name        = "${var.environment}-bedrock-sg"
  description = "Private interface for the RDS Tool to reach Bedrock"
  vpc_id      = local.vpc_id

  tags = {
    Name = "${var.environment}-bedrock-sg"
  }
}

# RDS METADATA STORE SECURITY GROUP
# Firewall for the RDS instance hosting department contact metadata.
resource "aws_security_group" "rds_metadata" {
  name        = "${var.environment}-rds-metadata-sg-v2"
  description = "Allows authorized data services to query the Department Contacts database"
  vpc_id      = local.vpc_id

  tags = {
    Name = "${var.environment}-rds-metadata-sg-v2"
  }
}

# RDS SEEDER SECURITY GROUP
# Group for the RDS Seeder Lambda (MCP Server).
resource "aws_security_group" "rds_seeder_sg" {
  name        = "${var.environment}-rds-seeder-sg"
  description = "Allows Data Services to reach RDS and AWS Services"
  vpc_id      = local.vpc_id
  tags = {
    Name = "${var.environment}-rds-seeder-sg"
  }
}

# RDS TOOL SECURITY GROUP
# Group for the RDS Tool Lambda (MCP Server).
resource "aws_security_group" "rds_tool_sg" {
  name        = "${var.environment}-rds-tool-sg"
  description = "Allows RDS Tool to reach RDS and AWS Services"
  vpc_id      = local.vpc_id
  tags = {
    Name = "${var.environment}-rds-tool-sg"
  }
}

# RDS INIT SECURITY GROUP
# Group for the RDS Init Lambda (MCP Server).
resource "aws_security_group" "rds_init_sg" {
  name        = "${var.environment}-rds-init-sg"
  description = "Allows RDS Init to reach RDS and AWS Services"
  vpc_id      = local.vpc_id
  tags = {
    Name = "${var.environment}-rds-init-sg"
  }
}

# KB SYNC SECURITY GROUP
# Group for the Knowledge Base synchronisation pipeline.
resource "aws_security_group" "kb_sync_sg" {
  name        = "${var.environment}-kb-sync-sg"
  description = "Security group for KB Sync Lambdas to access RDS and Bedrock"
  vpc_id      = local.vpc_id

  tags = {
    Name = "${var.environment}-kb-sync-sg"
  }
}

# ============ INGRESS RULES ======================================================

# Ingress rule allowing HTTPS traffic from the Orchestrator to VPC Endpoints.
resource "aws_security_group_rule" "allow_orchestrator_to_endpoints" {
  description              = "HTTPS from Orchestrator"
  type                     = "ingress"
  from_port                = 443
  to_port                  = 443
  protocol                 = "tcp"
  security_group_id        = aws_security_group.vpc_endpoints.id
  source_security_group_id = aws_security_group.orchestrator.id
}

# Allow Tooling & Seeder to reach RDS
resource "aws_security_group_rule" "allow_rds_seeder_to_rds" {
  description              = "PostgreSQL from RDS Seeder"
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = aws_security_group.rds_metadata.id
  source_security_group_id = aws_security_group.rds_seeder_sg.id
}

resource "aws_security_group_rule" "allow_rds_tool_to_rds" {
  description              = "PostgreSQL from RDS Tool"
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = aws_security_group.rds_metadata.id
  source_security_group_id = aws_security_group.rds_tool_sg.id
}

resource "aws_security_group_rule" "allow_rds_init_to_rds" {
  description              = "PostgreSQL from RDS Init"
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = aws_security_group.rds_metadata.id
  source_security_group_id = aws_security_group.rds_init_sg.id
}

resource "aws_security_group_rule" "allow_kb_sync_to_rds" {
  description              = "PostgreSQL from KB Sync Pipeline"
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = aws_security_group.rds_metadata.id
  source_security_group_id = aws_security_group.kb_sync_sg.id
}

# ----- Ingress rules allowing authorized data services to reach AWS service endpoints. -----

resource "aws_security_group_rule" "allow_rds_seeder_to_endpoints" {
  description              = "Allow RDS seeder to reach Bedrock/SecretsManager endpoints"
  type                     = "ingress"
  from_port                = 443
  to_port                  = 443
  protocol                 = "tcp"
  security_group_id        = aws_security_group.vpc_endpoints.id
  source_security_group_id = aws_security_group.rds_seeder_sg.id
}

resource "aws_security_group_rule" "allow_rds_tool_to_bedrock" {
  description              = "Allow RDS query tool to reach Bedrock endpoint"
  type                     = "ingress"
  from_port                = 443
  to_port                  = 443
  protocol                 = "tcp"
  security_group_id        = aws_security_group.bedrock.id
  source_security_group_id = aws_security_group.rds_tool_sg.id
}

resource "aws_security_group_rule" "allow_rds_init_to_secrets_manager" {
  description              = "Allow RDS init tool to reach SecretsManager endpoint"
  type                     = "ingress"
  from_port                = 443
  to_port                  = 443
  protocol                 = "tcp"
  security_group_id        = aws_security_group.secrets_manager.id
  source_security_group_id = aws_security_group.rds_init_sg.id
}

resource "aws_security_group_rule" "allow_kb_sync_to_endpoints" {
  description              = "Allow KB sync pipeline to reach Bedrock/SecretsManager endpoints"
  type                     = "ingress"
  from_port                = 443
  to_port                  = 443
  protocol                 = "tcp"
  security_group_id        = aws_security_group.vpc_endpoints.id
  source_security_group_id = aws_security_group.kb_sync_sg.id
}

# ======================= EGRESS RULES (INTERNAL) ===========================================

resource "aws_vpc_security_group_egress_rule" "allow_rds_init_to_rds" {
  description                  = "Allow outbound traffic from RDS Init to RDS"
  security_group_id            = aws_security_group.rds_init_sg.id
  referenced_security_group_id = aws_security_group.rds_metadata.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_vpc_security_group_egress_rule" "allow_rds_seeder_to_rds" {
  description                  = "Allow outbound traffic from RDS Seeder to RDS"
  security_group_id            = aws_security_group.rds_seeder_sg.id
  referenced_security_group_id = aws_security_group.rds_metadata.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_vpc_security_group_egress_rule" "allow_rds_tool_to_rds" {
  description                  = "Allow outbound traffic from RDS Tool to RDS"
  security_group_id            = aws_security_group.rds_tool_sg.id
  referenced_security_group_id = aws_security_group.rds_metadata.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_vpc_security_group_egress_rule" "allow_rds_init_to_secrets_manager" {
  description                  = "Allow outbound traffic from RDS Init to Secrets Manager"
  security_group_id            = aws_security_group.rds_init_sg.id
  referenced_security_group_id = aws_security_group.secrets_manager.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}

resource "aws_vpc_security_group_egress_rule" "allow_rds_seeder_to_vpc_endpoints" {
  description                  = "Allow outbound traffic from RDS Seeder to VPC endpoints"
  security_group_id            = aws_security_group.rds_seeder_sg.id
  referenced_security_group_id = aws_security_group.vpc_endpoints.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}

resource "aws_vpc_security_group_egress_rule" "allow_rds_tool_to_bedrock" {
  description                  = "Allow outbound traffic from RDS Tool to Bedrock"
  security_group_id            = aws_security_group.rds_tool_sg.id
  referenced_security_group_id = aws_security_group.bedrock.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}

# allow orchestrator to VPC endpints
resource "aws_vpc_security_group_egress_rule" "allow_orchestrator_to_vpc_endpoints" {
  description                  = "Allow outbound traffic from Orchestrator to VPC endpoints"
  security_group_id            = aws_security_group.orchestrator.id
  referenced_security_group_id = aws_security_group.vpc_endpoints.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}

# allow KB Sync Lambdas to RDS
resource "aws_vpc_security_group_egress_rule" "allow_kb_sync_lambdas_to_RDS" {
  description                  = "Allow outbound traffic from KB Sync Lambdas to RDS"
  security_group_id            = aws_security_group.kb_sync_sg.id
  referenced_security_group_id = aws_security_group.rds_metadata.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

# allow KB Sync Lambdas to Bedrock
resource "aws_vpc_security_group_egress_rule" "allow_kb_sync_lambdas_to_bedrock" {
  description                  = "Allow outbound traffic from KB Sync Lambdas to Bedrock"
  security_group_id            = aws_security_group.kb_sync_sg.id
  referenced_security_group_id = aws_security_group.bedrock.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}

# ========================= EGRESS RULES (EXTERNAL) ==================================

# fetch the official AWS-managed S3 Prefix List
data "aws_prefix_list" "s3" {
  name = "com.amazonaws.${var.aws_region}.s3"
}

# allow external egress from RDS seeder to S3 only, by referencing the AWS-managed S3 Prefix List
resource "aws_vpc_security_group_egress_rule" "rds_seeder_external_https" {
  security_group_id = aws_security_group.rds_seeder_sg.id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  prefix_list_id    = data.aws_prefix_list.s3.id
}
