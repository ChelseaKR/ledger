# versions.tf — pinned provider versions for a reproducible deploy.
terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Project   = "ledger"
      Component = "demo"
      ManagedBy = "terraform"
    }
  }
}
