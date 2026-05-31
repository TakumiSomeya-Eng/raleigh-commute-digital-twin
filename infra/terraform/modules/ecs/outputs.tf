# Outputs from the ECS module — consumed by stepfn/ module.

output "cluster_arn" {
  description = "ARN of the ECS cluster (for Step Functions ECS RunTask)."
  value       = aws_ecs_cluster.main.arn
}

output "cluster_name" {
  description = "Name of the ECS cluster."
  value       = aws_ecs_cluster.main.name
}

output "task_definition_arns" {
  description = "Map of stage name -> task definition ARN (for Step Functions state machine)."
  value       = { for k, v in aws_ecs_task_definition.stage : k => v.arn }
}

output "log_group_name" {
  description = "CloudWatch log group name for ECS tasks."
  value       = aws_cloudwatch_log_group.ecs.name
}
