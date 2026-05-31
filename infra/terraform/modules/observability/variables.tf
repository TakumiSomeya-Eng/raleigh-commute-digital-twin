# Input variables for the observability module (FR-12.6, T6.8)

variable "env" {
  description = "Environment name (dev or prod)."
  type        = string

  validation {
    condition     = contains(["dev", "prod"], var.env)
    error_message = "env must be 'dev' or 'prod'."
  }
}

variable "alert_email" {
  description = "Email address for cost alerts."
  type        = string
}

variable "monthly_cost_limit_usd" {
  description = "Hard monthly cost ceiling in USD (BR-4: stop processing if exceeded)."
  type        = number
  default     = 50
}

variable "tags" {
  description = "Additional tags merged onto every resource."
  type        = map(string)
  default     = {}
}
