# Outputs from the EventBridge module.

output "rule_arn" {
  description = "ARN of the EventBridge rule."
  value       = aws_cloudwatch_event_rule.s3_raw_upload.arn
}

output "rule_name" {
  description = "Name of the EventBridge rule."
  value       = aws_cloudwatch_event_rule.s3_raw_upload.name
}
