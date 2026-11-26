# -----------------------------------------------------------------------------
# DVLA CRM (Content Guru Storm) - Shared Dev Credentials
# -----------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "dvla_storm_dev_credentials" {
  name        = "shared/crm-creds/dvla-storm-dev"
  description = "Shared Content Guru Storm API credentials and endpoint config for DVLA dev/sandbox workspaces"

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Component = "crm-integration"
  }
}

resource "aws_secretsmanager_secret_version" "dvla_storm_dev_val" {
  secret_id = aws_secretsmanager_secret.dvla_storm_dev_credentials.id
  secret_string = jsonencode({
    api_key    = "REPLACE_IN_CONSOLE"
    api_secret = "REPLACE_IN_CONSOLE"
    account_id = "REPLACE_IN_CONSOLE"
    base_url   = "REPLACE_IN_CONSOLE"
  })

  lifecycle {
    ignore_changes = [secret_string]
  }
}
