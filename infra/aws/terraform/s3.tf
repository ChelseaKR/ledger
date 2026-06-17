# s3.tf — a private bucket that ships the application source to the instance.
#
# The instance builds the Docker image from source on first boot, so it needs the
# repository. Rather than give the box a git deploy key for the private repo, the
# deploy bundles the tracked source into a zip (see deploy.sh, which runs
# `git archive`) and uploads it here; the instance pulls it with its IAM role.
# This keeps the demo self-contained: no registry, no external git auth.

resource "random_id" "suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "source" {
  bucket        = "${var.name_prefix}-source-${random_id.suffix.hex}"
  force_destroy = true # demo: allow `terraform destroy` to remove the bundle
}

resource "aws_s3_bucket_public_access_block" "source" {
  bucket                  = aws_s3_bucket.source.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "source" {
  bucket = aws_s3_bucket.source.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# The source bundle built by deploy.sh (`git archive` of the committed tree, so
# no .git/.venv/runtime data is shipped). filemd5 makes the object update whenever
# the bundle changes, which (via user_data's hash) reprovisions on the next apply.
resource "aws_s3_object" "source" {
  bucket = aws_s3_bucket.source.id
  key    = "source-${filemd5("${path.module}/.build/source.zip")}.zip"
  source = "${path.module}/.build/source.zip"
  etag   = filemd5("${path.module}/.build/source.zip")
}
