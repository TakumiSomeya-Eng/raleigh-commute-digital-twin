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

output "ecs_cluster_arn" {
  description = "ECS cluster ARN (for Step Functions)."
  value       = module.ecs.cluster_arn
}

output "ecs_task_definition_arns" {
  description = "Map of stage -> task definition ARN."
  value       = module.ecs.task_definition_arns
}

output "state_machine_arn" {
  description = "Step Functions state machine ARN."
  value       = module.stepfn.state_machine_arn
}

output "eventbridge_rule_name" {
  description = "EventBridge rule that triggers the pipeline."
  value       = module.eventbridge.rule_name
}
