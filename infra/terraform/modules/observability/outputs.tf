# Outputs from the observability module.

output "cost_alert_topic_arn" {
  description = "ARN of the cost alert SNS topic."
  value       = aws_sns_topic.cost_alert.arn
}

output "budget_name" {
  description = "Name of the AWS Budget."
  value       = aws_budgets_budget.monthly.name
}
