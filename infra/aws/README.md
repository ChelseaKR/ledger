# ledger AWS demo — operator runbook

This stands up the **public showcase** of ledger on a single, inexpensive EC2 box:
the application behind Caddy with automatic Let's Encrypt TLS, reachable at a domain
you control. It is provisioned by the Terraform in `terraform/` and brought up by
`deploy.sh`.

---

## SAFETY: synthetic data only

> **This is a showcase, not a production archive.** The instance seeds itself with
> SYNTHETIC records (`LEDGER_DEMO_SEED=1`) so the interface has something to show.
> **Do not put real records, real contributors, or anything that can get a real
> person hurt into this deployment.** It has not cleared the bar a real archive must
> clear.

ledger holds its application-layer guarantees here as everywhere — holding a record
never outs the person who made it, and a corrupted copy never silently becomes the
truth — but a public, single-box demo is deliberately below the production bar. Before
trusting ledger with anything real, work through **`docs/ADOPTING.md`** (the
deployment-readiness checklist) and meet the production gates the user-research report
names as conditions for a real launch:

- an **independent security audit**, and the still-owed **independent WCAG 2.2 AA
  contrast audit** (`docs/accessibility/ACR.md`);
- a **second maintainer** — the report's chief sustainability concern is the single
  maintainer / bus factor (USER-RESEARCH T14), echoed by the funder and librarian
  personas;
- **real assistive-technology testing** with real disabled users on real hardware —
  the research used synthetic personas and CLI evaluation, never a screen reader on a
  live device, and explicitly defers to real testing before launch;
- **governance and legal controls** — two-steward governance, identity-unseal split
  from the steward role, full-disk encryption, off-box replicas, and the rest of the
  operational (not code) controls in `docs/ADOPTING.md` and `docs/GOVERNANCE.md`.

Treat everything below as a way to *look at* ledger running, not a template for
operating a live archive.

---

## What this deploys

A single self-contained VPC with one public subnet and one box. Nothing else faces the
network.

- **EC2 `t4g.small`** (Amazon Linux 2023, arm64 / Graviton), root EBS (gp3, 20 GiB,
  **encrypted**).
- **`docker compose`** on that box runs two services:
  - **`ledger`** — the app, built from source on first boot, on an internal Docker
    network only (it publishes no host ports);
  - **`caddy`** — TLS-terminating reverse proxy, the only thing bound to the host
    (80/443), obtaining and renewing a **Let's Encrypt** certificate automatically.
- **Elastic IP** — a stable public address so DNS does not change when the instance is
  replaced.
- **Private S3 bucket** — ships the application source bundle (`git archive` of the
  committed tree, built by `deploy.sh`) to the box; public access fully blocked,
  SSE-encrypted.
- **SSM Parameter Store (SecureString)** — the demo's vault key and claim secret,
  **generated on the instance at first boot** and never written to Terraform state or
  baked into the AMI. A replaced instance reuses them, so the synthetic archive
  survives instance replacement.
- **IMDSv2 only** (`http_tokens = required`); least-privilege instance IAM role.
- **SSM Session Manager** for shell access — **no inbound SSH by default**.

```
                       Internet
                          │  80 / 443
                          ▼
                  ┌───────────────┐   Elastic IP
                  │  VPC 10.20/16 │
                  │  public subnet│
                  │  ┌──────────┐ │
        ACME  ◄───┤  │  caddy   │ │   TLS termination, HSTS, Let's Encrypt
   (Let's Encrypt)│  └────┬─────┘ │
                  │       │ internal docker network
                  │  ┌────┴─────┐ │
                  │  │  ledger  │ │   app :8000 (no host port)
                  │  └────┬─────┘ │
                  │       │ archive-data volume
                  └───────┼───────┘
                          │ IAM role (no SSH)
              ┌───────────┼────────────┐
              ▼           ▼            ▼
        S3 (source)   SSM Params   SSM Session
                      (secrets)     Manager
```

---

## Prerequisites

- An **AWS account and credentials** configured for the CLI/Terraform (e.g. a profile,
  env vars, or SSO). **This deploys real, billable resources** — see Cost below.
- **Terraform >= 1.5** (the configuration pins `>= 1.5.0`, AWS provider `~> 5.40`).
- A **domain you control**, so Caddy can obtain a certificate for it.
- Optionally the **AWS Session Manager plugin**, to open a shell with
  `aws ssm start-session`. (You can also reach the box from the AWS console without it.)

---

## Deploy

```sh
# from the repo root
cp infra/aws/terraform/terraform.tfvars.example \
   infra/aws/terraform/terraform.tfvars
```

Edit `infra/aws/terraform/terraform.tfvars` and set at least:

```hcl
domain     = "ledger.example.com"   # a domain you control
acme_email = "you@example.com"      # Let's Encrypt expiry notices
```

Optional overrides (defaults shown in the example file): `aws_region`,
`instance_type`, `archive_name`, and `route53_zone_id` to automate the DNS record.

Then:

```sh
chmod +x infra/aws/deploy.sh infra/aws/destroy.sh
./infra/aws/deploy.sh
```

`deploy.sh` builds the source bundle (`git archive` → `infra/aws/terraform/.build/source.zip`),
then runs `terraform apply`. On success it prints the outputs, including:

- **`elastic_ip`** — the public IP to point your domain at;
- **`url`** — `https://<domain>` (live once DNS resolves and the cert issues);
- **`instance_id`**, **`ssm_session_command`**, **`logs_hint`**, **`dns_instructions`**.

**Point DNS.** Create an **A record** for your domain pointing at the `elastic_ip`
output (TTL 300). If you set `route53_zone_id` in your tfvars, this A record is created
for you and you can skip this step.

**Wait for the certificate.** Once DNS resolves to the box, Caddy issues the TLS
certificate within **~1 minute**. Then open **`https://<domain>`**.

> First boot (package install, Docker, image build, seeding) takes a few minutes after
> `apply` returns. If the site is not up immediately, watch provisioning — see
> Operating it.

---

## Cost

Approximate, `us-east-1`, on-demand, and **rough** — confirm against the AWS pricing
pages and your own usage; your region and account discounts will differ.

| Item | Rough monthly |
| --- | --- |
| EC2 `t4g.small` (on-demand, 24×7) | ~$12 |
| Root EBS, gp3, 20 GiB | ~$1.60 |
| Elastic IP (attached, in use) | ~$3.60 |
| S3 source bundle + SSM SecureString | negligible (cents) |
| **Total** | **~$12–16 / mo** |

Data transfer for a low-traffic demo is small but not always zero. An *unattached*
Elastic IP is also billed, which is one more reason to tear the demo down when you are
finished:

```sh
./infra/aws/destroy.sh
```

This removes everything Terraform created and **stops all charges** for the demo.

---

## Operating it

There is no SSH by default. Open a shell with Session Manager:

```sh
aws ssm start-session --target <instance_id> --region <aws_region>
# the exact command is the `ssm_session_command` output
```

In that session:

```sh
# Watch first-boot provisioning (cloud-init runs the user_data script):
sudo tail -f /var/log/cloud-init-output.log

# Once the stack is up, view application + Caddy logs:
cd /opt/ledger/app
sudo docker compose -f infra/aws/docker-compose.deploy.yml logs -f
```

The runtime env lives in a root-only `/opt/ledger/.env` (mode 600) that the first-boot
script writes from the SSM secrets and your tfvars; `docker compose` reads it via
`--env-file`. The demo **auto-seeds synthetic records** because `LEDGER_DEMO_SEED=1`,
so the archive has content to browse on the first visit. The `ledger` container's
health is checked against `http://127.0.0.1:8000/healthz` inside the container.

To pick up new code, re-run `./infra/aws/deploy.sh`: a changed source bundle changes
the S3 object key and the box reprovisions on the next apply.

---

## Teardown

```sh
./infra/aws/destroy.sh
```

Removes the instance, Elastic IP, VPC, S3 bucket (force-destroyed), IAM role, and SSM
parameters — the whole demo — and ends billing for it.

---

## Security notes for the demo

These cover the demo *deployment itself*; they are not the production bar (that is the
SAFETY section and `docs/ADOPTING.md`).

- **Secrets stay out of Terraform state.** The vault key and claim secret are generated
  *on the instance* at first boot and stored as **SSM SecureString** parameters
  (encrypted with the account's default SSM KMS key). They are never in state, never in
  the AMI, never on a command line.
- **Root EBS is encrypted** at rest.
- **No inbound SSH by default.** Administer via SSM Session Manager. SSH (port 22) is
  only opened if you explicitly set `allow_ssh_cidr`.
- **Only 80/443 are open** to the internet (80 for the ACME challenge and the redirect
  to HTTPS). `ledger` publishes no host port; only Caddy faces the host.
- **IMDSv2 is required**, and the instance role is least-privilege: SSM management, read
  the source object, and read/write its own `/<name_prefix>/*` SSM parameters — nothing
  more.
- **Caddy adds HSTS** and terminates TLS in front of the app, which is exactly the
  reverse-proxy posture `docs/ADOPTING.md` requires before exposing the app off
  loopback.
- **The application's own guarantees still apply.** ledger's no-outing line and
  consent-based disclosure hold here as elsewhere — but remember that "sealed"
  *content* (as opposed to vault-encrypted *identity*) is readable by stewards and by
  anyone with raw disk or replica access. With synthetic data that is harmless; it is
  exactly why this box must never hold real records.
