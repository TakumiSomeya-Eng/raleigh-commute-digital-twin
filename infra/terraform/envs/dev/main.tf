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
    bucket = "rct-tfstate-takumi2026"
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

module "ecr" {
  source = "../../modules/ecr"

  env = "dev"
}

module "iam" {
  source = "../../modules/iam"

  env                = "dev"
  data_bucket_arn    = module.s3.bucket_arn
  ecr_repository_arn = module.ecr.python_worker_repository_arn
  aws_account_id     = var.aws_account_id
  aws_region         = var.region
}
