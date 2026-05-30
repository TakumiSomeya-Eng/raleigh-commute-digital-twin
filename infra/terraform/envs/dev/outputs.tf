output "data_bucket_name" {
  description = "Name of the dev data bucket."
  value       = module.s3.bucket_name
}

output "data_bucket_arn" {
  description = "ARN of the dev data bucket."
  value       = module.s3.bucket_arn
}
