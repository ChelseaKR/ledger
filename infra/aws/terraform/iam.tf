# iam.tf — a least-privilege instance role.
#
# The instance needs to: be managed via SSM Session Manager (no inbound SSH);
# read the source bundle from its private S3 bucket; and read/write the two demo
# secrets in SSM Parameter Store (generated on first boot, so they never enter
# Terraform state). Nothing more.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = "${var.name_prefix}-instance"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

# Session Manager: shell access without opening SSH to the world.
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "instance" {
  # Read the source bundle.
  statement {
    sid       = "ReadSource"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.source.arn}/*"]
  }

  # Generate-once-then-reuse the demo secrets in Parameter Store. Keeping them in
  # SSM (not Terraform state, not the AMI) means a replaced instance reuses the
  # same vault key, so the synthetic archive survives instance replacement.
  statement {
    sid     = "DemoSecrets"
    actions = ["ssm:GetParameter", "ssm:PutParameter"]
    resources = [
      "arn:aws:ssm:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:parameter/${var.name_prefix}/*",
    ]
  }

  # SecureString parameters are encrypted with the account's default SSM KMS key.
  statement {
    sid       = "DecryptSsm"
    actions   = ["kms:Decrypt", "kms:Encrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${data.aws_region.current.name}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "instance" {
  name   = "${var.name_prefix}-instance"
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.instance.json
}

resource "aws_iam_instance_profile" "instance" {
  name = "${var.name_prefix}-instance"
  role = aws_iam_role.instance.name
}
