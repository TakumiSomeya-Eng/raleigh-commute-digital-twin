# Input variables for the IAM module (FR-12.7 — least privilege)

variable "env" {
  description = "Environment name (dev or prod)."
  type        = string

  validation {
    condition     = contains(["dev", "prod"], var.env)
    error_message = "env must be 'dev' or 'prod'."
  }
}

variable "data_bucket_arn" {
  description = "ARN of the rct-data-{suffix} S3 bucket (from module.s3.bucket_arn)."
  type        = string
}

variable "ecr_repository_arn" {
  description = "ARN of the rct/python-worker ECR repository (from module.ecr.python_worker_repository_arn)."
  type        = string
}

variable "valhalla_ecr_repository_arn" {
  description = "ARN of the rct/valhalla ECR repository (from module.ecr.valhalla_repository_arn)."
  type        = string
}

variable "github_org" {
  description = "GitHub organisation or username (e.g. TakumiSomeya-Eng)."
  type        = string
  default     = "TakumiSomeya-Eng"
}

variable "github_repo" {
  description = "GitHub repository name (e.g. raleigh-commute-digital-twin)."
  type        = string
  default     = "raleigh-commute-digital-twin"
}

variable "aws_account_id" {
  description = "12-digit AWS account ID. Used to scope ARNs precisely."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must be exactly 12 digits."
  }
}

variable "aws_region" {
  description = "AWS region (e.g. us-east-1)."
  type        = string
  default     = "us-east-1"
}

variable "tags" {
  description = "Additional tags merged onto every resource."
  type        = map(string)
  default     = {}
}
