# Input variables for the ECR module (FR-12.2)

variable "env" {
  description = "Environment name (dev or prod). Used for tagging."
  type        = string

  validation {
    condition     = contains(["dev", "prod"], var.env)
    error_message = "env must be either 'dev' or 'prod'."
  }
}

variable "tags" {
  description = "Additional tags merged onto every resource."
  type        = map(string)
  default     = {}
}
