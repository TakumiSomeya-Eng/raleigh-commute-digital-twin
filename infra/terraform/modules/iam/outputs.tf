# Outputs from the IAM module — consumed by ecs/ and stepfn/ modules.

output "gha_role_arn" {
  description = "ARN of the GitHub Actions OIDC role (for deploy.yaml)."
  value       = aws_iam_role.gha.arn
}

output "fargate_task_role_arn" {
  description = "ARN of the ECS task role (taskRoleArn in task definition)."
  value       = aws_iam_role.fargate_task.arn
}

output "fargate_execution_role_arn" {
  description = "ARN of the ECS execution role (executionRoleArn in task definition)."
  value       = aws_iam_role.fargate_execution.arn
}

output "stepfn_role_arn" {
  description = "ARN of the Step Functions role (roleArn in state machine)."
  value       = aws_iam_role.stepfn.arn
}
