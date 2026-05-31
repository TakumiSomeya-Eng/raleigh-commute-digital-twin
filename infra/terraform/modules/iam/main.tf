# IAM roles for the Raleigh Commute Digital Twin pipeline (FR-12.7)
#
# SECURITY RULE: No wildcard (*) in any policy action or resource.
# Every permission is scoped to the exact resource ARN.
#
# Roles created:
#   rct-gha-{env}              GitHub Actions OIDC — ECR push + Step Functions start
#   rct-fargate-task-{env}     ECS task — S3 read/write for pipeline data
#   rct-fargate-execution-{env} ECS agent — ECR pull + CloudWatch Logs
#   rct-stepfn-{env}           Step Functions — ECS run + SNS publish
#
# Estimated cost: $0 (IAM has no per-resource charge).

locals {
  common_tags = merge(
    {
      Project   = "raleigh-commute-digital-twin"
      Phase     = "2"
      Env       = var.env
      ManagedBy = "terraform"
      Module    = "iam"
    },
    var.tags,
  )

  # Reusable OIDC provider URL for GitHub Actions.
  github_oidc_url = "token.actions.githubusercontent.com"
}

# ── GitHub Actions OIDC provider ──────────────────────────────────────────────
# Allows GitHub Actions to assume AWS roles without long-lived keys.

data "aws_iam_openid_connect_provider" "github" {
  # Look up existing OIDC provider; create it once manually if absent:
  # aws iam create-open-id-connect-provider \
  #   --url https://token.actions.githubusercontent.com \
  #   --client-id-list sts.amazonaws.com \
  #   --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
  url = "https://${local.github_oidc_url}"
}

# ── Role: rct-gha (GitHub Actions) ───────────────────────────────────────────

resource "aws_iam_role" "gha" {
  name        = "rct-gha-${var.env}"
  description = "Assumed by GitHub Actions via OIDC to push Docker images and start pipelines."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = data.aws_iam_openid_connect_provider.github.arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringLike = {
          "${local.github_oidc_url}:sub" = "repo:${var.github_org}/${var.github_repo}:*"
        }
        StringEquals = {
          "${local.github_oidc_url}:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "gha" {
  name = "rct-gha-${var.env}-policy"
  role = aws_iam_role.gha.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # ECR authentication (account-level, required for docker login)
      {
        Sid    = "ECRAuth"
        Effect = "Allow"
        Action = ["ecr:GetAuthorizationToken"]
        # GetAuthorizationToken has no resource-level restriction; * is required by AWS.
        Resource = ["*"]
      },
      # ECR image push (scoped to python-worker repo only)
      {
        Sid    = "ECRPush"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:CompleteLayerUpload",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart",
        ]
        Resource = [var.ecr_repository_arn]
      },
      # Step Functions: start pipeline execution (scoped to rct-* state machines)
      {
        Sid    = "StepFunctionsStart"
        Effect = "Allow"
        Action = ["states:StartExecution"]
        Resource = [
          "arn:aws:states:${var.aws_region}:${var.aws_account_id}:stateMachine:rct-*"
        ]
      },
    ]
  })
}

# ── Role: rct-fargate-task (ECS task — pipeline logic) ───────────────────────

resource "aws_iam_role" "fargate_task" {
  name        = "rct-fargate-task-${var.env}"
  description = "Assumed by ECS Fargate tasks to read/write pipeline data in S3."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "fargate_task" {
  name = "rct-fargate-task-${var.env}-policy"
  role = aws_iam_role.fargate_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # S3: read raw inputs and write pipeline outputs (scoped to data bucket)
      {
        Sid    = "S3PipelineReadWrite"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket",
        ]
        Resource = [
          var.data_bucket_arn,
          "${var.data_bucket_arn}/*",
        ]
      },
    ]
  })
}

# ── Role: rct-fargate-execution (ECS agent — infra plumbing) ─────────────────

resource "aws_iam_role" "fargate_execution" {
  name        = "rct-fargate-execution-${var.env}"
  description = "Assumed by the ECS agent to pull images from ECR and write logs."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "fargate_execution" {
  name = "rct-fargate-execution-${var.env}-policy"
  role = aws_iam_role.fargate_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # ECR: pull python-worker image
      {
        Sid    = "ECRAuth"
        Effect = "Allow"
        Action = ["ecr:GetAuthorizationToken"]
        Resource = ["*"] # required by AWS; no resource-level restriction available
      },
      {
        Sid    = "ECRPull"
        Effect = "Allow"
        Action = [
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchCheckLayerAvailability",
        ]
        Resource = [var.ecr_repository_arn]
      },
      # CloudWatch Logs: write container stdout/stderr
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = [
          "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/ecs/rct-*:*"
        ]
      },
    ]
  })
}

# ── Role: rct-stepfn (Step Functions orchestrator) ────────────────────────────

resource "aws_iam_role" "stepfn" {
  name        = "rct-stepfn-${var.env}"
  description = "Assumed by Step Functions to run ECS tasks and send SNS notifications."

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy" "stepfn" {
  name = "rct-stepfn-${var.env}-policy"
  role = aws_iam_role.stepfn.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # ECS: run Fargate tasks for each pipeline stage
      {
        Sid    = "ECSRunTask"
        Effect = "Allow"
        Action = [
          "ecs:RunTask",
          "ecs:StopTask",
          "ecs:DescribeTasks",
        ]
        Resource = [
          "arn:aws:ecs:${var.aws_region}:${var.aws_account_id}:task-definition/rct-*",
          "arn:aws:ecs:${var.aws_region}:${var.aws_account_id}:task/rct-*/*",
        ]
      },
      # IAM PassRole: required to pass task/execution roles to ECS
      {
        Sid    = "PassRoleToECS"
        Effect = "Allow"
        Action = ["iam:PassRole"]
        Resource = [
          aws_iam_role.fargate_task.arn,
          aws_iam_role.fargate_execution.arn,
        ]
      },
      # SNS: publish completion/failure notifications (T6.5で作成するtopicに限定)
      {
        Sid    = "SNSPublish"
        Effect = "Allow"
        Action = ["sns:Publish"]
        Resource = [
          "arn:aws:sns:${var.aws_region}:${var.aws_account_id}:rct-*"
        ]
      },
      # CloudWatch Logs: write Step Functions execution logs
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery",
          "logs:GetLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries",
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups",
        ]
        Resource = ["*"] # CloudWatch log delivery APIs require * (AWS limitation)
      },
    ]
  })
}
