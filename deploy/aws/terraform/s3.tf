resource "aws_s3_bucket" "runs" {
  bucket = "${var.project}-runs-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_versioning" "runs" {
  bucket = aws_s3_bucket.runs.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "runs" {
  bucket = aws_s3_bucket.runs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "runs" {
  bucket = aws_s3_bucket.runs.id
  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

resource "aws_s3_bucket_public_access_block" "runs" {
  bucket                  = aws_s3_bucket.runs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
