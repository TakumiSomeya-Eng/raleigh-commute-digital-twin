# S3 bucket for the Raleigh Commute Digital Twin pipeline (FR-12.1)
#
# Prefix layout (created implicitly by writers, documented here):
#   raw/{trip_id}/         user-uploaded Sensor Logger CSVs (immutable, BR-3)
#   processed/{trip_id}/   aligned_100hz.parquet
#   synthetic/{trip_id}/   synthetic scenarios (-> Glacier after 30 days)
#   fused/{trip_id}/       fused_ekf.parquet / fused_ukf.parquet
#   ideal/{trip_id}/       reference_path, ideal_speed, ideal_trajectory
#   scores/{trip_id}/      score.json
#   reports/{trip_id}/     report.html, index.html
#
# Estimated cost: < $1/month at expected volume (~20 trips, few hundred MB).

locals {
  bucket_name = "rct-data-${var.bucket_suffix}"

  common_tags = merge(
    {
      Project   = "raleigh-commute-digital-twin"
      Phase     = "2"
      Env       = var.env
      ManagedBy = "terraform"
      Module    = "s3"
    },
    var.tags,
  )
}

resource "aws_s3_bucket" "data" {
  bucket = local.bucket_name
  tags   = local.common_tags
}

# BR-3: Raw files are immutable — versioning enabled for audit + reprocessing.
resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Privacy rule: block ALL public access (Domain §Privacy).
resource "aws_s3_bucket_public_access_block" "data" {
  bucket = aws_s3_bucket.data.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Encrypt at rest with SSE-S3 (AES256) by default.
resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

# Lifecycle: archive synthetic/ to Glacier after N days (FR-12.1).
resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id

  # Depends on versioning being applied first.
  depends_on = [aws_s3_bucket_versioning.data]

  rule {
    id     = "synthetic-to-glacier"
    status = "Enabled"

    filter {
      prefix = "synthetic/"
    }

    transition {
      days          = var.synthetic_glacier_after_days
      storage_class = "GLACIER"
    }
  }

  # Clean up old non-current versions after 90 days to control storage cost.
  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

# EventBridge notifications enabled — required for S3 PutObject -> EventBridge
# -> Step Functions trigger (Interaction hypothesis, wired in T6.6).
resource "aws_s3_bucket_notification" "data" {
  bucket      = aws_s3_bucket.data.id
  eventbridge = true
}
