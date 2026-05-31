# Step Functions state machine + SNS notification (FR-12.4, Interaction hypothesis)
#
# Pipeline flow (Interaction hypothesis confirmed 2026-05-30):
#   S3 upload -> EventBridge -> StartExecution -> (T6.6)
#   ingest -> fuse -> ideal -> score -> report -> SNS notify
#
# On success: email with score + report.html S3 link
# On failure: email with failed stage name + error message (VL-5)
#
# Retry policy per stage: max 3 attempts, exponential backoff 30s base (OQ-2 resolved)
#
# Estimated cost: ~$0.0005/execution (20 state transitions × $0.000025)

locals {
  machine_name = "rct-pipeline-${var.env}"
  sns_name     = "rct-notify-${var.env}"

  common_tags = merge(
    {
      Project   = "raleigh-commute-digital-twin"
      Phase     = "2"
      Env       = var.env
      ManagedBy = "terraform"
      Module    = "stepfn"
    },
    var.tags,
  )

  # Fargate network config injected into each ECS RunTask state.
  network_config = {
    AwsvpcConfiguration = {
      Subnets        = var.subnet_ids
      SecurityGroups = var.security_group_ids
      AssignPublicIp = "ENABLED" # required for Fargate in default VPC (no NAT gateway)
    }
  }

  # Standard retry policy for all Fargate stages (OQ-2).
  retry_policy = [
    {
      ErrorEquals     = ["States.TaskFailed", "States.Timeout"]
      IntervalSeconds = 30
      MaxAttempts     = 3
      BackoffRate     = 2.0
    }
  ]
}

# ── SNS Topic + Email Subscription ───────────────────────────────────────────

resource "aws_sns_topic" "notify" {
  name = local.sns_name
  tags = local.common_tags
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.notify.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ── Step Functions Log Group ──────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "stepfn" {
  name              = "/aws/states/rct-pipeline-${var.env}"
  retention_in_days = 30
  tags              = local.common_tags
}

# ── Security Group for Fargate tasks ─────────────────────────────────────────
# Allow all outbound (S3, ECR, CloudWatch via HTTPS). No inbound needed.

resource "aws_security_group" "fargate" {
  name        = "rct-fargate-${var.env}"
  description = "Egress-only security group for RCT Fargate pipeline tasks"
  vpc_id      = data.aws_subnet.first.vpc_id

  egress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTPS to S3, ECR, CloudWatch"
  }

  tags = local.common_tags
}

data "aws_subnet" "first" {
  id = var.subnet_ids[0]
}

# ── Step Functions State Machine ──────────────────────────────────────────────

resource "aws_sfn_state_machine" "pipeline" {
  name     = local.machine_name
  role_arn = var.stepfn_role_arn
  type     = "STANDARD" # supports >5min jobs (Express = 5min max)

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.stepfn.arn}:*"
    include_execution_data = true
    level                  = "ERROR"
  }

  # State machine definition — ASL (Amazon States Language)
  definition = jsonencode({
    Comment = "RCT pipeline: ingest -> fuse -> ideal -> score -> report -> notify"
    StartAt = "Ingest"

    States = {

      # ── Stage 1: Ingest ──────────────────────────────────────────────────
      Ingest = {
        Type     = "Task"
        Resource = "arn:aws:states:::ecs:runTask.sync"
        Parameters = {
          Cluster        = var.cluster_arn
          TaskDefinition = var.task_definition_arns["ingest"]
          LaunchType     = "FARGATE"
          NetworkConfiguration = local.network_config
          Overrides = {
            ContainerOverrides = [{
              Name = "rct-ingest"
              Environment = [
                { Name = "TRIP_ID", "Value.$" = "$.trip_id" },
                { Name = "S3_BUCKET", Value = var.data_bucket_name },
              ]
            }]
          }
        }
        Retry    = local.retry_policy
        Catch    = [{ ErrorEquals = ["States.ALL"], Next = "NotifyFailure", ResultPath = "$.error" }]
        Next     = "Fuse"
        ResultPath = null
      }

      # ── Stage 2: Fuse (py_ekf.py) ───────────────────────────────────────
      Fuse = {
        Type     = "Task"
        Resource = "arn:aws:states:::ecs:runTask.sync"
        Parameters = {
          Cluster        = var.cluster_arn
          TaskDefinition = var.task_definition_arns["fuse"]
          LaunchType     = "FARGATE"
          NetworkConfiguration = local.network_config
          Overrides = {
            ContainerOverrides = [{
              Name = "rct-fuse"
              Environment = [
                { Name = "TRIP_ID", "Value.$" = "$.trip_id" },
                { Name = "S3_BUCKET", Value = var.data_bucket_name },
              ]
            }]
          }
        }
        Retry    = local.retry_policy
        Catch    = [{ ErrorEquals = ["States.ALL"], Next = "NotifyFailure", ResultPath = "$.error" }]
        Next     = "Ideal"
        ResultPath = null
      }

      # ── Stage 3: Ideal driver ────────────────────────────────────────────
      Ideal = {
        Type     = "Task"
        Resource = "arn:aws:states:::ecs:runTask.sync"
        Parameters = {
          Cluster        = var.cluster_arn
          TaskDefinition = var.task_definition_arns["ideal"]
          LaunchType     = "FARGATE"
          NetworkConfiguration = local.network_config
          Overrides = {
            ContainerOverrides = [{
              Name = "rct-ideal"
              Environment = [
                { Name = "TRIP_ID", "Value.$" = "$.trip_id" },
                { Name = "S3_BUCKET", Value = var.data_bucket_name },
              ]
            }]
          }
        }
        Retry    = local.retry_policy
        Catch    = [{ ErrorEquals = ["States.ALL"], Next = "NotifyFailure", ResultPath = "$.error" }]
        Next     = "Score"
        ResultPath = null
      }

      # ── Stage 4: Score ───────────────────────────────────────────────────
      Score = {
        Type     = "Task"
        Resource = "arn:aws:states:::ecs:runTask.sync"
        Parameters = {
          Cluster        = var.cluster_arn
          TaskDefinition = var.task_definition_arns["score"]
          LaunchType     = "FARGATE"
          NetworkConfiguration = local.network_config
          Overrides = {
            ContainerOverrides = [{
              Name = "rct-score"
              Environment = [
                { Name = "TRIP_ID", "Value.$" = "$.trip_id" },
                { Name = "S3_BUCKET", Value = var.data_bucket_name },
              ]
            }]
          }
        }
        Retry    = local.retry_policy
        Catch    = [{ ErrorEquals = ["States.ALL"], Next = "NotifyFailure", ResultPath = "$.error" }]
        Next     = "Report"
        ResultPath = null
      }

      # ── Stage 5: Report ──────────────────────────────────────────────────
      Report = {
        Type     = "Task"
        Resource = "arn:aws:states:::ecs:runTask.sync"
        Parameters = {
          Cluster        = var.cluster_arn
          TaskDefinition = var.task_definition_arns["report"]
          LaunchType     = "FARGATE"
          NetworkConfiguration = local.network_config
          Overrides = {
            ContainerOverrides = [{
              Name = "rct-report"
              Environment = [
                { Name = "TRIP_ID", "Value.$" = "$.trip_id" },
                { Name = "S3_BUCKET", Value = var.data_bucket_name },
              ]
            }]
          }
        }
        Retry    = local.retry_policy
        Catch    = [{ ErrorEquals = ["States.ALL"], Next = "NotifyFailure", ResultPath = "$.error" }]
        Next     = "NotifySuccess"
        ResultPath = null
      }

      # ── Success notification ─────────────────────────────────────────────
      # Email: score + report.html S3 link (VL-5: Option C)
      NotifySuccess = {
        Type     = "Task"
        Resource = "arn:aws:states:::sns:publish"
        Parameters = {
          TopicArn = aws_sns_topic.notify.arn
          Subject  = "✅ RCT Pipeline Complete"
          "Message.$" = "States.Format('Trip: {}\n\nReport: https://${var.data_bucket_name}.s3.${var.aws_region}.amazonaws.com/reports/{}/report.html\n\nCheck score.json at: s3://${var.data_bucket_name}/scores/{}/score.json', $.trip_id, $.trip_id, $.trip_id)"
        }
        End = true
      }

      # ── Failure notification ─────────────────────────────────────────────
      # Email: failed stage + error details (VL-5: Option C)
      NotifyFailure = {
        Type     = "Task"
        Resource = "arn:aws:states:::sns:publish"
        Parameters = {
          TopicArn = aws_sns_topic.notify.arn
          Subject  = "❌ RCT Pipeline Failed"
          "Message.$" = "States.Format('Trip: {}\n\nError: {}\n\nCheck CloudWatch Logs: /ecs/rct-${var.env}', $.trip_id, $.error)"
        }
        Next = "PipelineFailed"
      }

      PipelineFailed = {
        Type  = "Fail"
        Error = "PipelineFailed"
        Cause = "One or more pipeline stages failed. See SNS notification for details."
      }
    }
  })

  tags = local.common_tags
}
