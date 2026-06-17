# outputs.tf — what you need after `apply`.

output "elastic_ip" {
  description = "The instance's public elastic IP. Point your domain's A record here."
  value       = aws_eip.this.public_ip
}

output "url" {
  description = "The demo URL (live once DNS points at elastic_ip and Caddy has issued a cert)."
  value       = "https://${var.domain}"
}

output "dns_instructions" {
  description = "How to point your domain at the demo."
  value       = var.route53_zone_id == "" ? "Create an A record: ${var.domain} -> ${aws_eip.this.public_ip} (TTL 300). Caddy issues the TLS cert within ~1 minute of DNS resolving." : "Route 53 A record for ${var.domain} -> ${aws_eip.this.public_ip} was created automatically."
}

output "ssm_session_command" {
  description = "Open a shell on the box without SSH (requires the AWS CLI + Session Manager plugin)."
  value       = "aws ssm start-session --target ${aws_instance.this.id} --region ${var.aws_region}"
}

output "instance_id" {
  description = "EC2 instance id."
  value       = aws_instance.this.id
}

output "logs_hint" {
  description = "Where to watch first-boot provisioning."
  value       = "In an SSM session: sudo tail -f /var/log/cloud-init-output.log ; then: cd /opt/ledger/app && sudo docker compose -f infra/aws/docker-compose.deploy.yml logs -f"
}
