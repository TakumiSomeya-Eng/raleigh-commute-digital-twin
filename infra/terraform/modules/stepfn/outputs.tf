# Outputs from the Step Functions module — consumed by EventBridge (T6.6).

output "state_machine_arn" {
  description = "ARN of the pipeline state machine (for EventBridge rule target)."
  value       = aws_sfn_state_machine.pipeline.arn
}

output "state_machine_name" {
  description = "Name of the state machine."
  value       = aws_sfn_state_machine.pipeline.name
}

output "sns_topic_arn" {
  description = "ARN of the SNS notification topic."
  value       = aws_sns_topic.notify.arn
}

output "fargate_security_group_id" {
  description = "Security group ID for Fargate tasks (egress-only)."
  value       = aws_security_group.fargate.id
}
