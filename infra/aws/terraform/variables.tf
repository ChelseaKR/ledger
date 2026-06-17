# variables.tf — everything an operator sets to stand up the demo.
#
# This deploys a SHOWCASE with SYNTHETIC data on a single inexpensive box. It is
# NOT a production setup for real contributor records — see docs/ADOPTING.md and
# the security gates in infra/aws/README.md before ever holding real data.

variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "domain" {
  description = "Fully-qualified domain for the demo (e.g. ledger.example.com). Caddy gets a Let's Encrypt cert for it once DNS points at the instance's elastic IP."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9.-]+\\.[a-z]{2,}$", var.domain))
    error_message = "domain must be a bare FQDN like ledger.example.com (no scheme, no path)."
  }
}

variable "acme_email" {
  description = "Contact email for the Let's Encrypt / ACME account (expiry notices)."
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type. The default is a small ARM (Graviton) box; the image is multi-arch."
  type        = string
  default     = "t4g.small"
}

variable "name_prefix" {
  description = "Prefix for the names/tags of created resources."
  type        = string
  default     = "ledger-demo"
}

variable "archive_name" {
  description = "Human-facing name of the demo archive."
  type        = string
  default     = "Rosewater Community Archive (demo)"
}

variable "root_volume_gb" {
  description = "Root EBS volume size (GiB). The synthetic archive is tiny; this is mostly headroom for the OS, Docker, and the built image."
  type        = number
  default     = 20
}

variable "route53_zone_id" {
  description = "Optional Route 53 hosted-zone id. If set, an A record for var.domain is created pointing at the instance. If empty, point DNS yourself using the elastic_ip output."
  type        = string
  default     = ""
}

variable "allow_ssh_cidr" {
  description = "Optional CIDR allowed to reach SSH (port 22). Leave empty to disable inbound SSH entirely and administer via SSM Session Manager (recommended)."
  type        = string
  default     = ""
}

variable "allowed_http_cidr" {
  description = "CIDR allowed to reach the public site (80/443). Defaults to the whole internet, as a public demo intends."
  type        = string
  default     = "0.0.0.0/0"
}
