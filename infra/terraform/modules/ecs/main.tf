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
  # NOTE: ENTRYPOINT in Dockerfile is ["python3.11"], so command must NOT include "python".
  stages = {
    ingest = {
      cpu     = 256
      memory  = 512
      command = ["-m", "data_engine", "ingest"]
    }
    fuse = {
      cpu     = 512
      memory  = 1024
      command = ["scripts/py_ekf.py"]
    }
    ideal = {
      cpu     = 1024  # Valhalla needs more memory
      memory  = 2048
      command = ["-m", "ideal_driver", "run"]
    }
    score = {
      cpu     = 256
      memory  = 512
      command = ["-m", "scoring", "run"]
    }
    report = {
      cpu     = 256
      memory  = 512
      command = ["-m", "reporting", "run"]
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
      environment = concat(
        [
          { name = "S3_BUCKET", value = var.data_bucket_name },
          { name = "AWS_DEFAULT_REGION", value = var.aws_region },
          { name = "STAGE", value = each.key },
        ],
        # The ideal stage calls Valhalla for map-matching; inject its DNS name.
        each.key == "ideal" ? [
          { name = "VALHALLA_URL", value = "http://valhalla.${local.cluster_name}.local:8002" }
        ] : []
      )

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

# ── Cloud Map — private DNS namespace for service discovery ───────────────────
# Valhalla is reachable at http://valhalla.rct-dev.local:8002 from any ECS task
# in the same VPC.

resource "aws_service_discovery_private_dns_namespace" "rct" {
  name        = "${local.cluster_name}.local"
  description = "Private DNS for RCT ECS services (Valhalla map-matching)"
  vpc         = data.aws_vpc.default.id

  tags = local.common_tags
}

resource "aws_service_discovery_service" "valhalla" {
  name = "valhalla"

  dns_config {
    namespace_id = aws_service_discovery_private_dns_namespace.rct.id

    dns_records {
      ttl  = 10
      type = "A"
    }

    routing_policy = "MULTIVALUE"
  }

  health_check_custom_config {
    failure_threshold = 1
  }

  tags = local.common_tags
}

# Use the default VPC (same one the Fargate tasks run in)
data "aws_vpc" "default" {
  default = true
}

# ── Valhalla Task Definition ──────────────────────────────────────────────────
# Always-on ECS Service; ~$42/month for 1 vCPU / 4 GB.
# Tiles are downloaded from S3 on container startup (~30s).

resource "aws_ecs_task_definition" "valhalla" {
  family                   = "rct-valhalla-${var.env}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 1024
  memory                   = 4096
  task_role_arn            = var.task_role_arn
  execution_role_arn       = var.execution_role_arn

  container_definitions = jsonencode([
    {
      name    = "rct-valhalla"
      image   = "${var.valhalla_ecr_repository_url}:latest"
      command = []

      environment = [
        { name = "S3_BUCKET", value = var.data_bucket_name },
        { name = "AWS_DEFAULT_REGION", value = var.aws_region },
      ]

      portMappings = [
        { containerPort = 8002, protocol = "tcp" }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = local.log_group
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "valhalla"
        }
      }

      essential = true
    }
  ])

  tags = merge(local.common_tags, { Stage = "valhalla" })
}

# ── Valhalla ECS Service (always-on) ─────────────────────────────────────────

resource "aws_ecs_service" "valhalla" {
  name            = "rct-valhalla-${var.env}"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.valhalla.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  # Allow in-place replacement during deployments
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = var.security_group_ids
    assign_public_ip = true
  }

  service_registries {
    registry_arn = aws_service_discovery_service.valhalla.arn
  }

  tags = merge(local.common_tags, { Stage = "valhalla" })
}
