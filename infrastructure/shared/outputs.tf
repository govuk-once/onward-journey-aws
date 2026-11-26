output "dvla_storm_credentials_secret_name" {
  description = "Secrets Manager secret name for shared DVLA Content Guru Storm API credentials"
  value       = aws_secretsmanager_secret.dvla_storm_dev_credentials.name
}
