# Dev environment composition (T6.1: S3 only; more modules added in T6.2+).

terraform {
  required_version = ">= 1.7.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.50"
    }
  }

  # Remote state backend. Create the tfstate bucket once, manually, before
  # the first `terraform init` (chicken-and-egg). See infra/README.md.
  backend "s3" {
    bucket = "rct-tfstate-CHANGEME"
    key    = "dev/terraform.tfstate"
    region = "us-east-1"
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project = "raleigh-commute-digital-twin"
      Env     = "dev"
    }
  }
}

module "s3" {
  source = "../../modules/s3"

  bucket_suffix = var.bucket_suffix
  env           = "dev"
}
