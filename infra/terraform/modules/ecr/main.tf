# ECR repository for the Python worker image (FR-12.2, MVP: python-worker only)
#
# ros2-worker is intentionally omitted (VL-1: py_ekf.py == C++ EKF accuracy,
# VL-2: EKS control plane $72/month > $50 cost ceiling).
# Add ros2-worker here when EKS is introduced post-MVP.
#
# Estimated cost: $0 for empty repo; ~$0.10/GB/month once images are pushed.
# A typical python-worker image is ~1.5 GB → ~$0.15/month.

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
