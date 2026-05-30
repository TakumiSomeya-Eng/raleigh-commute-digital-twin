# Input variables for the S3 module (FR-12.1)

variable "bucket_suffix" {
  description = "Globally-unique suffix appended to the bucket name (e.g. account id or random string). The full name becomes rct-data-{suffix}."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9-]{3,40}$", var.bucket_suffix))
    error_message = "bucket_suffix must be 3-40 chars, lowercase letters, digits, or hyphens."
  }
}

variable "env" {
  description = "Environment name (dev or prod). Used for tagging."
  type        = string

  validation {
    condition     = contains(["dev", "prod"], var.env)
    error_message = "env must be either 'dev' or 'prod'."
  }
}

variable "synthetic_glacier_after_days" {
  description = "Number of days after which objects under synthetic/ transition to Glacier (FR-12.1)."
  type        = number
  default     = 30
}

variable "tags" {
  description = "Additional tags merged onto every resource."
  type        = map(string)
  default     = {}
}
