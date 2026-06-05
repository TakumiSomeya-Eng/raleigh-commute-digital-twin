# Outputs from the ECR module — consumed by iam/ and ecs/ modules.

output "python_worker_repository_url" {
  description = "Full ECR URL used in docker push and ECS task definitions."
  value       = aws_ecr_repository.python_worker.repository_url
}

output "python_worker_repository_arn" {
  description = "ARN of the python-worker repository (for IAM policies)."
  value       = aws_ecr_repository.python_worker.arn
}

output "registry_id" {
  description = "AWS account ID that owns the registry (for docker login)."
  value       = aws_ecr_repository.python_worker.registry_id
}

output "valhalla_repository_url" {
  description = "Full ECR URL for the Valhalla image."
  value       = aws_ecr_repository.valhalla.repository_url
}

output "valhalla_repository_arn" {
  description = "ARN of the Valhalla ECR repository (for IAM policies)."
  value       = aws_ecr_repository.valhalla.arn
}
