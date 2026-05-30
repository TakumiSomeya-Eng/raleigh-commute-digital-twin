variable "region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

variable "bucket_suffix" {
  description = "Globally-unique suffix for the data bucket (e.g. your AWS account id)."
  type        = string
}
