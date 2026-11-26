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

# # Read-only lookup for the shared Storm API credentials managed in infrastructure/shared/secrets.tf
data "aws_secretsmanager_secret" "dvla_storm_credentials" {
  name = var.dvla_storm_secret_name
}

# Initial random UUID used as the query-string token (?token=<uuid>) on inbound Storm webhooks
resource "random_uuid" "dvla_storm_inbound_webhook_url_token" {}

# Metadata container for this environment's inbound webhook token registry
resource "aws_secretsmanager_secret" "dvla_storm_inbound_webhook_url_token" {
  name        = "${var.environment}/crm-creds/dvla-storm-inbound-webhook-url-token"
  description = "URL query token registry for authenticating inbound DVLA Content Guru Storm webhooks"
}

# Populates the initial { "<token>": "<caller_id>" } map read by crm-wh-token-authorizer.
resource "aws_secretsmanager_secret_version" "dvla_storm_inbound_webhook_url_token_val" {
  secret_id = aws_secretsmanager_secret.dvla_storm_inbound_webhook_url_token.id
  secret_string = jsonencode({
    (random_uuid.dvla_storm_inbound_webhook_url_token.result) = "dvla-storm"
  })

  # Ensures future terraform applies never overwrite manual token rotations.
  lifecycle {
    ignore_changes = [secret_string]
  }
}

# -----------------------------------------------------------------------------
# WebSocket Client Handshake Authorisation
# -----------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "ws_auth_tokens" {
  name        = "${var.environment}/websocket/auth-tokens"
  description = "Maps generated WebSocket token UUIDs to actor UUIDs"
}

resource "aws_secretsmanager_secret_version" "ws_auth_tokens_val" {
  secret_id = aws_secretsmanager_secret.ws_auth_tokens.id

  secret_string = jsonencode({
    for idx in local.tester_indices :
    random_uuid.ws_tokens[idx].result => random_uuid.actor_ids[idx].result
  })
}

# -----------------------------------------------------------------------------
# Tester Credentials
# -----------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "tester_credentials" {
  name        = "${var.environment}/frontend/tester-credentials"
  description = "Basic Auth credentials for frontend user testing"
}

resource "aws_secretsmanager_secret_version" "tester_credentials_val" {
  secret_id = aws_secretsmanager_secret.tester_credentials.id

  secret_string = jsonencode({
    for idx in local.tester_indices : "Tester ${idx}" => {
      username = random_pet.tester_usernames[idx].id
      password = random_password.tester_passwords[idx].result
    }
  })
}
