/**
- PURPOSE: Secure Credential Management and Environment Secrets.
- This file manages the metadata and containers for sensitive credentials
- required by the Onward Journey stack.
 *
- NOTE: To prevent sensitive values from being stored in the Terraform state
- file, actual secret values (passwords, API keys, etc.) should be set
- manually via the AWS Console or CLI after the initial resource creation.
 */

# -----------------------------------------------------------------------------
# Department Contacts Database
# -----------------------------------------------------------------------------

# Metadata container for the department contacts database password
resource "aws_secretsmanager_secret" "dept_contacts_db_password" {
  name        = "${var.environment}-dept-contacts-db-password"
  description = "Password for the ${var.environment} department contacts database"

  lifecycle {
    prevent_destroy = true
  }
}

# The Secret Value (To be populated manually in Console or via CLI for safety)
data "aws_secretsmanager_secret_version" "dept_contacts_db_password" {
  secret_id = aws_secretsmanager_secret.dept_contacts_db_password.id
}

# -----------------------------------------------------------------------------
# Home Office CRM (Genesys Cloud)
# -----------------------------------------------------------------------------

# Metadata container for Home Office Genesys OAuth credentials
resource "aws_secretsmanager_secret" "ho_genesys_credentials" {
  # Naming by Department + Provider
  name        = "${var.environment}/crm-creds/home-office-genesys"
  description = "OAuth credentials for the Home Office Genesys Cloud instance"

  lifecycle {
    prevent_destroy = true
  }
}

# Placeholder structure for Home Office CRM (manually populated in Console/CLI)
resource "aws_secretsmanager_secret_version" "ho_crm_val" {
  secret_id = aws_secretsmanager_secret.ho_genesys_credentials.id
  secret_string = jsonencode({
    client_id      = "REPLACE_IN_CONSOLE"
    client_secret  = "REPLACE_IN_CONSOLE"
    org_id         = "REPLACE_IN_CONSOLE"
    external_kb_id = "REPLACE_IN_CONSOLE"
    queue_id       = "REPLACE IN CONSOLE"
    deploy_id      = "REPLACE IN CONSOLE"
  })
  lifecycle {
    ignore_changes = [secret_string]
  }
}

# -----------------------------------------------------------------------------
# DVLA CRM (Content Guru Storm)
# -----------------------------------------------------------------------------

# Metadata container for DVLA Content Guru API credentials and endpoint config
resource "aws_secretsmanager_secret" "dvla_storm_credentials" {
  name        = "${var.environment}/crm-creds/dvla-storm"
  description = "API credentials and endpoint config for the DVLA Content Guru Storm instance"

  lifecycle {
    prevent_destroy = true
  }
}

# Placeholder structure for DVLA CRM (manually populated in Console/CLI)
resource "aws_secretsmanager_secret_version" "dvla_storm_val" {
  secret_id = aws_secretsmanager_secret.dvla_storm_credentials.id
  secret_string = jsonencode({
    api_key    = "REPLACE_IN_CONSOLE"
    account_id = "REPLACE_IN_CONSOLE"
    base_url   = "REPLACE_IN_CONSOLE"
  })
  lifecycle {
    ignore_changes = [secret_string]
  }
}

# -----------------------------------------------------------------------------
# WebSocket Client Handshake Authorisation
# -----------------------------------------------------------------------------

# Generate secure random UUIDs for the initial deployment to prevent hardcoded
# placeholder credentials from being exposed in source control.
resource "random_uuid" "default_ws_token" {}
resource "random_uuid" "default_actor_id" {}

resource "aws_secretsmanager_secret" "ws_auth_tokens" {
  name        = "${var.environment}/websocket/auth-tokens"
  description = "Pre-shared authorisation tokens and actor_id mapping for WebSocket $connect"
}

resource "aws_secretsmanager_secret_version" "ws_auth_tokens_val" {
  secret_id = aws_secretsmanager_secret.ws_auth_tokens.id

  # We inject the dynamically generated UUIDs as the initial secure state.
  # The lifecycle block ensures that subsequent terraform applies will not
  # overwrite any manual token rotations or future cohort token mappings.
  secret_string = jsonencode({
    (random_uuid.default_ws_token.result) = random_uuid.default_actor_id.result
  })

  lifecycle {
    ignore_changes = [secret_string]
  }
}
