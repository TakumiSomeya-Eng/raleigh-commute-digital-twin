# ECR repositories for the pipeline images (FR-12.2)
#
# python-worker: ingest / fuse / ideal / score / report stages
# valhalla:      Valhalla routing engine (always-on ECS service, ~$42/month)
#
# ros2-worker is intentionally omitted (VL-1/VL-2).
#
# Estimated cost: ~$0.15/month per image (1.5 GB python-worker; ~$0.20/month 2 GB valhalla).

locals {
  repo_name = "rct/python-worker"

  common_tags = merge(
    {
      Project   = "raleigh-commute-digital-twin"
      Phase     = "2"
      Env       = var.env
      ManagedBy = "terraform"
      Module    = "ecr"
    },
    var.tags,
  )
}

resource "aws_ecr_repository" "python_worker" {
  name                 = local.repo_name
  image_tag_mutability = "MUTABLE" # allows :latest tag for dev convenience

  # Scan each image on push for known CVEs.
  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.common_tags
}

# Keep the 10 most recent tagged images; expire untagged images after 1 day.
# This prevents storage cost from accumulating during active development.
resource "aws_ecr_lifecycle_policy" "python_worker" {
  repository = aws_ecr_repository.python_worker.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 1 day"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep only the 10 most recent tagged images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 10
        }
        action = { type = "expire" }
      }
    ]
  })
}

# ── Valhalla ECR repository ───────────────────────────────────────────────────
resource "aws_ecr_repository" "valhalla" {
  name                 = "rct/valhalla"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = local.common_tags
}

resource "aws_ecr_lifecycle_policy" "valhalla" {
  repository = aws_ecr_repository.valhalla.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 1 day"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep only the 5 most recent tagged images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 5
        }
        action = { type = "expire" }
      }
    ]
  })
}
