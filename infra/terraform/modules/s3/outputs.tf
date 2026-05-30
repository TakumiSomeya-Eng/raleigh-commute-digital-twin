# Outputs from the S3 module — consumed by ecs/, stepfn/, iam/ modules.

output "bucket_name" {
  description = "Name of the data bucket."
  value       = aws_s3_bucket.data.id
}

output "bucket_arn" {
  description = "ARN of the data bucket (for IAM policies)."
  value       = aws_s3_bucket.data.arn
}

output "bucket_regional_domain_name" {
  description = "Regional domain name (for presigned URLs in report links)."
  value       = aws_s3_bucket.data.bucket_regional_domain_name
}
