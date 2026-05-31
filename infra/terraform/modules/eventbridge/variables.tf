# Input variables for the EventBridge module (T6.6)

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

variable "data_bucket_name" {
  description = "Name of the S3 data bucket to watch (from module.s3)."
  type        = string
}

variable "state_machine_arn" {
  description = "ARN of the Step Functions state machine to trigger (from module.stepfn)."
  type        = string
}

variable "stepfn_role_arn" {
  description = "ARN of the Step Functions IAM role (reused as EventBridge target role)."
  type        = string
}

variable "tags" {
  description = "Additional tags merged onto every resource."
  type        = map(string)
  default     = {}
}
