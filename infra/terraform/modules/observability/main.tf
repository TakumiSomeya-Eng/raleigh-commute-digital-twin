# Cost budget + alert (FR-12.6, BR-4: $50/month ceiling)
#
# Two alerts:
#   80% of limit ($40): WARNING — approaching budget
#  100% of limit ($50): CRITICAL — budget exceeded, manually disable EventBridge rule
#
# Note: AWS Budgets alerts are not real-time; they fire within ~8 hours of threshold breach.
# For immediate protection, the EventBridge rule (T6.6) should be manually disabled
# when the CRITICAL alert fires.
#
# Estimated cost: $0 (AWS Budgets is free for first 2 budgets)

locals {
  common_tags = merge(
    {
      Project   = "raleigh-commute-digital-twin"
      Phase     = "2"
      Env       = var.env
      ManagedBy = "terraform"
      Module    = "observability"
    },
    var.tags,
  )
}

# ── SNS Topic for cost alerts (separate from pipeline notifications) ───────────

resource "aws_sns_topic" "cost_alert" {
  name = "rct-cost-alert-${var.env}"
  tags = local.common_tags
}

resource "aws_sns_topic_subscription" "cost_alert_email" {
  topic_arn = aws_sns_topic.cost_alert.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ── AWS Budget ────────────────────────────────────────────────────────────────

resource "aws_budgets_budget" "monthly" {
  name         = "rct-monthly-${var.env}"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_cost_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # WARNING at 80% ($40): slow down and investigate
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  # CRITICAL at 100% ($50): manually disable EventBridge rule (BR-4)
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
}
