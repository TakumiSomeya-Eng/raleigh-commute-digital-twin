# Input variables for the Step Functions + SNS module (FR-12.4, FR-12.5, T6.5+T6.6)

variable "env" {
  description = "Environment name (dev or prod)."
  type        = string

  validation {
    condition     = contains(["dev", "prod"], var.env)
    error_message = "env must be 'dev' or 'prod'."
  }
}

variable "aws_region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

variable "aws_account_id" {
  description = "12-digit AWS account ID."
  type        = string
}

variable "stepfn_role_arn" {
  description = "ARN of the Step Functions IAM role (from module.iam)."
  type        = string
}

variable "cluster_arn" {
  description = "ARN of the ECS cluster (from module.ecs)."
  type        = string
}

variable "task_definition_arns" {
  description = "Map of stage name -> task definition ARN (from module.ecs)."
  type        = map(string)
}

variable "task_role_arn" {
  description = "ARN of the Fargate task role (from module.iam)."
  type        = string
}

variable "execution_role_arn" {
  description = "ARN of the Fargate execution role (from module.iam)."
  type        = string
}

variable "subnet_ids" {
  description = "List of subnet IDs for Fargate tasks (default VPC subnets)."
  type        = list(string)
}

variable "security_group_ids" {
  description = "List of security group IDs for Fargate tasks."
  type        = list(string)
}

variable "data_bucket_name" {
  description = "Name of the S3 data bucket (from module.s3)."
  type        = string
}

variable "alert_email" {
  description = "Email address for pipeline completion/failure notifications (SNS)."
  type        = string
}

variable "log_group_name" {
  description = "CloudWatch log group name for ECS tasks (from module.ecs)."
  type        = string
}

variable "tags" {
  description = "Additional tags merged onto every resource."
  type        = map(string)
  default     = {}
}
