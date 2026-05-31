output "data_bucket_name" {
  description = "Name of the dev data bucket."
  value       = module.s3.bucket_name
}

output "data_bucket_arn" {
  description = "ARN of the dev data bucket."
  value       = module.s3.bucket_arn
}

output "python_worker_repository_url" {
  description = "ECR URL for the python-worker image."
  value       = module.ecr.python_worker_repository_url
}

output "gha_role_arn" {
  description = "GitHub Actions OIDC role ARN (paste into deploy.yaml)."
  value       = module.iam.gha_role_arn
}
