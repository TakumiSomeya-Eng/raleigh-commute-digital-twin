variable "region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
}

variable "bucket_suffix" {
  description = "Globally-unique suffix for the data bucket (e.g. your AWS account id)."
  type        = string
}

variable "aws_account_id" {
  description = "12-digit AWS account ID."
  type        = string
}

variable "subnet_ids" {
  description = "Subnet IDs for Fargate tasks (default VPC subnets)."
  type        = list(string)
  default = [
    "subnet-038efb3ce0a771fe4",
    "subnet-063e8169d2ad2cb31",
    "subnet-046c3cdbb782e266f",
    "subnet-0e0173e6efde036cd",
    "subnet-0d7fe7d408393eb89",
    "subnet-08002a583c24c07b6",
  ]
}

variable "alert_email" {
  description = "Email address for pipeline notifications (SNS)."
  type        = string
}
