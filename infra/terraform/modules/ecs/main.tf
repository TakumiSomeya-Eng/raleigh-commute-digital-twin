# ECS Fargate cluster and task definitions for the pipeline (FR-12.4)
#
# MVP pipeline stages (each runs as a separate Fargate task):
#   ingest   — data_engine: CSV -> aligned_100hz.parquet  (FR-1)
#   fuse     — py_ekf.py: Parquet -> fused_ekf.parquet    (FR-4, VL-1)
#   ideal    — ideal_driver + Valhalla map-match           (FR-9)
#   score    — scoring: -> score.json                      (FR-10)
#   report   — reporting: -> report.html                   (FR-11)
#
# EKS/ROS2 is intentionally omitted (VL-1: py_ekf.py == C++ EKF, VL-2: EKS $72/month).
#
# Estimated cost: $0 when idle; ~$0.002-0.004/trip when running (see .claude/skills/aws-infra.md).

locals {
  cluster_name = "rct-${var.env}"
  log_group    = "/ecs/rct-${var.env}"

  common_tags = merge(
    {
      Project   = "raleigh-commute-digital-twin"
      Phase     = "2"
      Env       = var.env
      ManagedBy = "terraform"
      Module    = "ecs"
    },
    var.tags,
  )

  # Pipeline stage definitions: name -> {cpu, memory, command}
  # cpu/memory in Fargate units (256 = 0.25 vCPU, 512 = 0.5 GB)
  stages = {
    ingest = {
      cpu     = 256
      memory  = 512
      command = ["python", "-m", "data_engine", "ingest"]
    }
    fuse = {
      cpu     = 512
      memory  = 1024
      command = ["python", "scripts/py_ekf.py"]
    }
    ideal = {
      cpu     = 1024  # Valhalla needs more memory
      memory  = 2048
      command = ["python", "-m", "ideal_driver", "run"]
    }
    score = {
      cpu     = 256
      memory  = 512
      command = ["python", "-m", "scoring", "run"]
    }
    report = {
      cpu     = 256
      memory  = 512
      command = ["python", "-m", "reporting", "run"]
    }
  }
}

# ── CloudWatch Log Group ───────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "ecs" {
  name              = local.log_group
  retention_in_days = 30  # keep logs for 30 days (cost vs debuggability)

  tags = local.common_tags
}

# ── ECS Cluster ───────────────────────────────────────────────────────────────

resource "aws_ecs_cluster" "main" {
  name = local.cluster_name

  setting {
    name  = "containerInsights"
    value = "disabled"  # enabled = $0.35/GB; disabled keeps cost low for MVP
  }

  tags = local.common_tags
}

resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = "FARGATE"
    weight            = 1
    base              = 0
  }
}

# ── ECS Task Definitions (one per pipeline stage) ─────────────────────────────

resource "aws_ecs_task_definition" "stage" {
  for_each = local.stages

  family                   = "rct-${each.key}-${var.env}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = each.value.cpu
  memory                   = each.value.memory
  task_role_arn            = var.task_role_arn
  execution_role_arn       = var.execution_role_arn

  container_definitions = jsonencode([
    {
      name    = "rct-${each.key}"
      image   = "${var.ecr_repository_url}:latest"
      command = each.value.command

      # Pipeline stage receives trip_id and bucket via environment variables.
      # Step Functions injects TRIP_ID at runtime (see stepfn module).
      environment = [
        { name = "S3_BUCKET", value = var.data_bucket_name },
        { name = "AWS_DEFAULT_REGION", value = var.aws_region },
        { name = "STAGE", value = each.key },
      ]

      # stdout/stderr -> CloudWatch Logs
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = local.log_group
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = each.key
        }
      }

      # No port mappings needed — Fargate tasks communicate via S3, not TCP.
      portMappings = []

      essential = true
    }
  ])

  tags = merge(local.common_tags, { Stage = each.key })
}
