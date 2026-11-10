## Dataset bucket storage
resource "aws_s3_bucket" "dataset_storage" {
  bucket = "onward-journey-${var.environment}-datasets"

  # Allow terraform to delete files when destroying for easy environment teardown
  # Dataset files get uploaded when creating a new environment
  force_destroy = true

  tags = {
    Name      = "${var.environment}-dataset-storage"
    Component = "dataset-storage"
  }
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

# Automatically clean up old versions to prevent storage bloat
resource "aws_s3_bucket_lifecycle_configuration" "dataset_cleanup" {
  bucket = aws_s3_bucket.dataset_storage.id

  rule {
    id     = "cleanup-old-dataset-versions"
    status = "Enabled"

    # Cleans up the old versions kept by versioning after 30 days
    noncurrent_version_expiration {
      noncurrent_days           = 30
      newer_noncurrent_versions = 3
    }

    expiration {
      expired_object_delete_marker = true
    }
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "dataset_storage" {
  bucket = aws_s3_bucket.dataset_storage.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Enforce HTTPS/TLS for dataset bucket
resource "aws_s3_bucket_policy" "dataset_enforce_tls" {
  bucket = aws_s3_bucket.dataset_storage.id
  policy = data.aws_iam_policy_document.dataset_enforce_tls.json
}

data "aws_iam_policy_document" "dataset_enforce_tls" {
  statement {
    sid    = "EnforceTLS"
    effect = "Deny"
    principals {
      type        = "AWS"
      identifiers = ["*"]
    }
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.dataset_storage.arn,
      "${aws_s3_bucket.dataset_storage.arn}/*"
    ]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
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
    Name      = "${var.environment}-infrastructure-storage"
    Component = "infrastructure-storage"
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

resource "aws_s3_bucket_versioning" "infrastructure" {
  bucket = aws_s3_bucket.infrastructure.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "infrastructure_cleanup" {
  bucket = aws_s3_bucket.infrastructure.id

  rule {
    id     = "cleanup-old-builds"
    status = "Enabled"

    filter {
      prefix = "builds/"
    }

    # Cleans up the old versions kept by versioning after 30 days
    noncurrent_version_expiration {
      noncurrent_days           = 30
      newer_noncurrent_versions = 3
    }

    expiration {
      expired_object_delete_marker = true
    }
  }
}

# Explicitly define infrastructure bucket encryption
resource "aws_s3_bucket_server_side_encryption_configuration" "infrastructure" {
  bucket = aws_s3_bucket.infrastructure.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Enforce HTTPS/TLS for infrastructure bucket
resource "aws_s3_bucket_policy" "infrastructure_enforce_tls" {
  bucket = aws_s3_bucket.infrastructure.id
  policy = data.aws_iam_policy_document.infrastructure_enforce_tls.json
}

data "aws_iam_policy_document" "infrastructure_enforce_tls" {
  statement {
    sid    = "EnforceTLS"
    effect = "Deny"
    principals {
      type        = "AWS"
      identifiers = ["*"]
    }
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.infrastructure.arn,
      "${aws_s3_bucket.infrastructure.arn}/*"
    ]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}
