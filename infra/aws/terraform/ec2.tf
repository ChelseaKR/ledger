# ec2.tf — the single demo box, its elastic IP, and optional DNS.

# Latest Amazon Linux 2023 (arm64, to match the t4g.* Graviton default).
data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

resource "aws_instance" "this" {
  ami                    = data.aws_ssm_parameter.al2023_arm64.value
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.web.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name

  # IMDSv2 only (no token-less metadata access) — a small, free hardening win.
  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = var.root_volume_gb
    encrypted   = true
  }

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    region       = var.aws_region
    bucket       = aws_s3_bucket.source.bucket
    source_key   = aws_s3_object.source.key
    domain       = var.domain
    acme_email   = var.acme_email
    archive_name = var.archive_name
    name_prefix  = var.name_prefix
  })

  # Re-run provisioning when the source bundle changes (new key) so a redeploy
  # picks up new code on the next apply.
  user_data_replace_on_change = true

  tags = { Name = "${var.name_prefix}" }

  depends_on = [aws_s3_object.source]
}

# A stable address so DNS does not have to change when the instance is replaced.
resource "aws_eip" "this" {
  instance = aws_instance.this.id
  domain   = "vpc"
  tags     = { Name = "${var.name_prefix}-eip" }
}

# Optional convenience: if a Route 53 zone is given, point the domain at the box.
resource "aws_route53_record" "this" {
  count   = var.route53_zone_id == "" ? 0 : 1
  zone_id = var.route53_zone_id
  name    = var.domain
  type    = "A"
  ttl     = 300
  records = [aws_eip.this.public_ip]
}
