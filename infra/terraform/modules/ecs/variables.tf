# Input variables for the ECS module (FR-12.4)

variable "env" {
  description = "Environment name (dev or prod)."
  type        = string

  validation {
    condition     = contains(["dev", "prod"], var.env)
    error_message = "env must be 'dev' or 'prod'."
  }
}

variable "aws_region" {
  description = "AWS region (e.g. us-east-1)."
  type        = string
  default     = "us-east-1"
}

variable "aws_account_id" {
  description = "12-digit AWS account ID."
  type        = string
}

variable "ecr_repository_url" {
  description = "Full ECR URL for the python-worker image (from module.ecr)."
  type        = string
}

variable "task_role_arn" {
  description = "ARN of the ECS task role (from module.iam.fargate_task_role_arn)."
  type        = string
}

variable "execution_role_arn" {
  description = "ARN of the ECS execution role (from module.iam.fargate_execution_role_arn)."
  type        = string
}

variable "data_bucket_name" {
  description = "Name of the S3 data bucket (from module.s3.bucket_name)."
  type        = string
}

variable "valhalla_ecr_repository_url" {
  description = "Full ECR URL for the Valhalla image (from module.ecr.valhalla_repository_url)."
  type        = string
}

variable "subnet_ids" {
  description = "List of VPC subnet IDs for the Valhalla ECS service."
  type        = list(string)
}

variable "security_group_ids" {
  description = "List of security group IDs for the Valhalla ECS service."
  type        = list(string)
}

variable "tags" {
  description = "Additional tags merged onto every resource."
  type        = map(string)
  default     = {}
}
