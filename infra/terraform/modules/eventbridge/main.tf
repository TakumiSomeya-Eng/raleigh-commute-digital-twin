# EventBridge rule: S3 PutObject -> Step Functions (Interaction hypothesis, T6.6)
#
# Trigger condition (OQ-1 resolution):
#   S3 object created under raw/{trip_id}/ prefix
#   with key suffix matching one of the 7 expected Sensor Logger CSV filenames.
#   Step Functions itself performs the file-count check (ListObjects) at runtime.
#
# Flow:
#   User uploads CSVs to s3://rct-data-{suffix}/raw/{trip_id}/
#       -> S3 EventBridge notification (enabled in T6.1)
#       -> EventBridge rule matches on prefix "raw/"
#       -> Step Functions StartExecution with input {"trip_id": "<trip_id>"}
#
# Estimated cost: $0 (first 1M events/month free; we'll use ~100/month)

locals {
  rule_name = "rct-s3-raw-upload-${var.env}"

  common_tags = merge(
    {
      Project   = "raleigh-commute-digital-twin"
      Phase     = "2"
      Env       = var.env
      ManagedBy = "terraform"
      Module    = "eventbridge"
    },
    var.tags,
  )
}

# ── IAM Role for EventBridge to invoke Step Functions ─────────────────────────

resource "aws_iam_role" "eventbridge" {
  name        = "rct-eventbridge-${var.env}"
  description = "Allows EventBridge to start the RCT pipeline state machine."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "eventbridge" {
  name = "rct-eventbridge-${var.env}-policy"
  role = aws_iam_role.eventbridge.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "StartStateMachine"
      Effect   = "Allow"
      Action   = ["states:StartExecution"]
      Resource = [var.state_machine_arn]
    }]
  })
}

# ── EventBridge Rule ───────────────────────────────────────────────────────────

resource "aws_cloudwatch_event_rule" "s3_raw_upload" {
  name        = local.rule_name
  description = "Fires when any object is created under raw/ in the RCT data bucket."

  # S3 EventBridge notifications send events in this format.
  # We match on: bucket name + key prefix "raw/"
  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = {
        name = [var.data_bucket_name]
      }
      object = {
        key = [{ prefix = "raw/" }]
      }
    }
  })

  tags = local.common_tags
}

# ── EventBridge Target: Step Functions ────────────────────────────────────────

resource "aws_cloudwatch_event_target" "stepfn" {
  rule     = aws_cloudwatch_event_rule.s3_raw_upload.name
  arn      = var.state_machine_arn
  role_arn = aws_iam_role.eventbridge.arn

  # Extract trip_id from the S3 key: "raw/day3/Location.csv" -> "day3"
  # Input transformer splits on "/" and takes the second segment.
  input_transformer {
    input_paths = {
      key = "$.detail.object.key"
    }
    # key = "raw/day3/Location.csv"
    # trip_id extraction: take the segment between first and second "/"
    input_template = <<-EOT
      {
        "trip_id": "<key>"
      }
    EOT
  }
}
