## Dataset bucket storage
resource "aws_s3_bucket" "dataset_storage" {
  bucket = "onward-journey-${var.environment}-datasets"

  # Allow terraform to delete files when destroying for easy environment teardown
  # Dataset files get uploaded when creating a new environment
  force_destroy = true
}

resource "aws_s3_bucket_versioning" "dataset_storage" {
  bucket = aws_s3_bucket.dataset_storage.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Explicitly block public access to dataset s3 bucket
resource "aws_s3_bucket_public_access_block" "dataset_storage" {
  bucket = aws_s3_bucket.dataset_storage.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Upload mock data files to s3
resource "aws_s3_object" "mock_data_upload" {
  for_each = local.mock_data_files # Iterate over every file found by the fileset function in locals.tf
  bucket   = aws_s3_bucket.dataset_storage.id
  key      = "mock/${each.value}"
  source   = "${path.module}/mock_data/${each.value}"
  etag     = filemd5("${path.module}/mock_data/${each.value}") # Terraform uses this to detect if the local file has changed. If it changes, Terraform will update the S3 object on 'apply'.
}

## Bucket for per workspace infrastructure (i.e. Agentcore Runtime build zips)
resource "aws_s3_bucket" "infrastructure" {
  bucket = "onward-journey-${var.environment}-infrastructure"

  # Allow Terraform to delete bucket and builds during environment teardown
  force_destroy = true

  tags = {
    Name = "${var.environment}-infrastructure-storage"
    Tier = "infrastructure"
  }
}

# Explicitly block public access to infrastructure bucket
resource "aws_s3_bucket_public_access_block" "infrastructure_storage" {
  bucket = aws_s3_bucket.infrastructure.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Automatically delete old builds after 30 days
resource "aws_s3_bucket_lifecycle_configuration" "infrastructure_cleanup" {
  bucket = aws_s3_bucket.infrastructure.id

  rule {
    id     = "cleanup-old-builds"
    status = "Enabled"

    filter {
      prefix = "builds/"
    }

    expiration {
      days = 30
    }
  }
}
