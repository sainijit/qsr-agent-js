# AWS deployment: single VM (Phase 10)

This prepares a low-cost, single-VM deployment of the existing Docker Compose stack.
**Nothing here has been created in AWS.** Every command below is for the operator to run after approving the cost.
There is no managed database, cache, broker, orchestrator or load balancer (they would need a separate architecture decision).

| File | Purpose |
|---|---|
| `docker-compose.yml` + `docker-compose.prod.yml` | production stack (Caddy edge, internal data network, limits, immutable image tags) |
| `deploy/aws/env.production.example` | every variable with placeholders; preflight refuses while any `<...>` remains |
| `deploy/aws/render-env.sh` | SSM Parameter Store → `/opt/zuvo/.env` (mode 600) via the instance role |
| `deploy/aws/docker-compose.datadir.yml` | optional: Postgres/Kafka/Redis/Caddy data on a dedicated disk (`ZUVO_DATA_DIR`) |
| `deploy/aws/docker-compose.awslogs.yml` | optional: container logs → CloudWatch Logs |
| `deploy/caddy/Caddyfile` | HTTPS (Let's Encrypt), security headers, dev endpoints blocked |
| `scripts/backup/*` | backups → S3 (SSE-KMS), see `docs/backup-and-restore.md` |
| `scripts/deploy/*` | preflight / deploy / health-check / smoke-test / rollback (Phase 11, 13) |
| `services/migrator/set_password.py` | set a user's password from a prompt or stdin (never argv), audited |

## 0. Clean VM → running application (ordered walkthrough)

Each step links to the detail. Do them in this order; stop at the first failure. Steps 1–3 cost money and need approval.

| # | Step | Command / where | Done when |
|---|---|---|---|
| 1 | **Prerequisites**: AWS account, region `ap-south-1`, a domain you control, Meta app + WhatsApp number, Gemini API key, a git remote the VM can read (deploy key) | — | all available |
| 2 | **Firewall**: security group (443 tcp/udp, 80 tcp, no 22) | §4, first block of §5 | SG exists |
| 3 | **IAM + S3 + KMS**: role `zuvo-prod-vm`, bucket with lifecycle, `alias/zuvo-backup` | §6, `docs/backup-and-restore.md` §3 | policies attached |
| 4 | **VM**: launch (IMDSv2, encrypted gp3, termination protection), Elastic IP | §5 | `aws ssm start-session` works |
| 5 | **Host**: UTC, updates, Docker + compose ≥ 2.24, AWS CLI, 2 GB swap, user `zuvo`, `/opt/zuvo`, `/var/backups/zuvo`, `/var/log/zuvo`, clone | second block of §5 | `docker compose version` |
| 6 | **Config**: every variable of `env.production.example` into SSM `/zuvo/prod/*` (SecureString for secrets; generate with `openssl rand -hex 32`). Keep `META_DRY_RUN=true`, leave `ZUVO_ONBOARD_DEFAULT_PASSWORD` unset | `deploy/aws/render-env.sh --region ap-south-1` | `/opt/zuvo/.env` mode 600, no `<...>` left |
| 7 | **DNS**: A record `app.<domain>` → Elastic IP; wait until it resolves | §5 "DNS and HTTPS" 1–2 | `dig +short` = Elastic IP |
| 8 | **Preflight** | `scripts/deploy/preflight.sh` | 0 errors |
| 9 | **Deploy** (builds the 11 images, starts data services, runs migrations + tenant bundles, starts the app in order, health-checks, records the release). On an empty database there is nothing to back up, so the backup step is skipped; every later release takes a verified backup first | `cd /opt/zuvo && git checkout --detach <release-tag> && scripts/deploy/deploy.sh` | `deployed <tag> (schema …)` |
| 10 | **HTTPS + smoke** | `curl -sI https://app.<domain>/` (308, HSTS), then `scripts/deploy/smoke-test.sh` | `all checks passed` |
| 11 | **First owner password** (bundle users have no usable password) | `scripts/deploy/dcp run --rm --no-deps migrator python set_password.py owner@<tenant-domain>` (interactive prompt; ≥ 12 chars) | `{"updated": true, …}`; dashboard login works |
| 12 | **Backups**: install `/etc/cron.d/zuvo-backup`, then one manual S3 run | `docs/backup-and-restore.md` §4, end of §6 | `upload=ok`, `verify-backup.sh` prints `manifest match` |
| 13 | **Alerting**: SNS topic or webhook, `zuvo-watch.sh` cron, external `StatusCheckFailed` alarm + uptime check | `docs/runbooks/README.md`, §7 | test alert received |
| 14 | **Meta webhook**: callback URL + verify token, subscribe to `messages`; send one test message | §5 "DNS and HTTPS" 4 | message in the dashboard, reply held (dry run) |
| 15 | **Go live**: set `META_DRY_RUN=false` in SSM, `render-env.sh`, `scripts/deploy/deploy.sh` (same tag; services whose configuration changed are recreated) | — | a real reply is delivered |
| 16 | **Load gate on the VM** (before more tenants): `docs/load-testing.md` steady + burst with the Gemini mock | `docs/load-testing.md` §2 | memory peak and lag within §4 |

**Rollback**: `scripts/deploy/rollback.sh` restarts the previous release's images (application only, refused if the schema changed).
Data recovery is `docs/backup-and-restore.md` §5c and needs approval. Details: `docs/deployment.md` "Rollback" and "Failed deployment".

## 1. Sizing: measured

The full production stack was running on the dev host (`-p zuvo-prodtest`, `APP_ENV=production`, idle with 4 tenants and light test traffic). `docker stats`, 2026-10-19:

| Container | RSS | Limit | | Container | RSS | Limit |
|---|---|---|---|---|---|---|
| kafka (JVM, heap ≤ 512 MB) | 540 MiB | 900 | | dispatch | 62 MiB | 256 |
| zuvo-ai-rag | 92 MiB | 384 | | lead-service | 60 MiB | 256 |
| agent-worker | 82 MiB | 256 | | persister | 60 MiB | 256 |
| dashboard-api | 69 MiB | 384 | | followup-scheduler | 55 MiB | 256 |
| ai-router | 55 MiB | 256 | | gateway | 51 MiB | 256 |
| postgres | 48 MiB | 512 | | frontend (nginx) | 13 MiB | 64 |
| caddy | 12 MiB | 128 | | redis | 4 MiB | 128 |
| **total** | **≈ 1.2 GiB** | **4.3 GB of limits** | | | | |

CPU is under 2 % total at idle, because all AI inference is remote (Gemini). Disk use: Postgres 78 MB; Kafka 5.5 MB real. Kafka index files are
sparse, so `docker system df` reports about 7.6 GB, but actual usage is small. The Kafka retention cap of 32 MB × partitions is an upper bound, not an expected size.
Images take about 3 GB.

**Conclusion:** 4 GB RAM is the smallest size that is not undersized, *with* a 2 GB swap file (§5). The limits
(4.3 GB) are ceilings, not reservations. The real risk is Kafka (JVM) plus Postgres under load and the nightly full-verify
scratch Postgres. **2 GB instances are rejected**: Kafka alone needs about 0.55 GB, and the stack needs about 1.2 GB at idle before page cache.
**Gate:** before go-live, the Phase 14 load test must show peak usage < 3.0 GiB, no OOM kills (`dmesg`, `docker inspect …OOMKilled`) and
swap-in < 10 MB/s sustained on the chosen 4 GB VM. If any of those fails, move to 8 GB (§7: about 10 minutes of downtime).

**Load test (2026-10-19, dev host, `docs/load-testing.md`):** peak 1.38 GiB across idle, startup, 1 and 5 msg/s, a 200-message burst, a Gemini
outage, the API and RAG scenarios. No OOM kills or restarts. The gate passes on memory, but it has **not yet been repeated on a real 4 GB / 2 vCPU VM**:
run `steady --rate 1` and `burst` there before go-live. Kafka's healthcheck now runs every 30 s in production, which saves about 5 % of a core of idle CPU and t4g credits.

## 2. Cost estimate (ap-south-1 Mumbai)

Prices come from the **AWS Price List API** (on-demand, Linux), retrieved 2026-10-19. Publication dates: EC2 2026-10-15,
Lightsail 2026-09-15, VPC 2026-10-16, S3 2026-10-15, CloudWatch 2026-10-07, Data Transfer 2026-09-16. Re-check them before ordering:
prices change, and these figures are not a quote.

Unit prices used: t4g.medium $0.0224/h · t3a.medium $0.0246/h · t4g.large $0.0448/h · gp3 $0.0912/GB-mo (3000 IOPS /
125 MB/s included) · public IPv4 $0.005/h · EBS snapshot $0.05/GB-mo · S3 Standard $0.025/GB-mo, PUT $0.005/1k · CloudWatch Logs
ingest $0.67/GB, storage $0.03/GB-mo · internet egress $0.1093/GB beyond the free allowance · Lightsail bundles 4 GB $0.03225/h, 8 GB
$0.05913/h (including IPv4, SSD and transfer allowance).

**Assumptions:**
- 730 h/month.
- USD→INR ₹86 (verify on the invoice date).
- **18 % GST** on AWS India (AISPL) invoices.
- Egress under 100 GB/month: WhatsApp API calls plus dashboard traffic are a few GB, inside AWS's monthly free data-transfer-out allowance. This allowance is *not* in the price list, so it is NOT VERIFIED; at worst it adds $0.11/GB.
- Database under 1 GB.
- A KMS customer-managed key at ~$1/month (NOT VERIFIED; using the AWS-managed `aws/s3` key costs nothing).

| Option | vCPU / RAM | Compute | Disk | IPv4 | Backups (S3+KMS+snap) | Logs (opt.) | **USD/mo** | **₹/mo incl. GST** |
|---|---|---|---|---|---|---|---|---|
| **EC2 t4g.medium (Graviton) — recommended** | 2 / 4 GB | 16.35 | 40 GB gp3 3.65 | 3.65 | ~1.3 | 1.4 | **≈ 26.3** | **≈ ₹2,670** |
| EC2 t3a.medium (x86 fallback) | 2 / 4 GB | 17.96 | 3.65 | 3.65 | ~1.3 | 1.4 | ≈ 27.9 | ≈ ₹2,830 |
| Lightsail 4 GB | 2 / 4 GB | 23.54 (incl. 80 GB SSD, 2 TB transfer, IPv4) | — | — | ~1.3 | — | ≈ 24.8 | ≈ ₹2,520 |
| EC2 t4g.large | 2 / 8 GB | 32.70 | 3.65 | 3.65 | ~1.3 | 1.4 | ≈ 42.7 | ≈ ₹4,330 |
| Lightsail 8 GB | 2 / 8 GB | 43.16 | — | — | ~1.3 | — | ≈ 44.5 | ≈ ₹4,510 |

Without the optional CloudWatch logs and snapshots, t4g.medium costs ≈ $24.7, or ≈ ₹2,500 including GST. A 1-year Savings Plan or Reserved Instance lowers compute
by roughly 30–40 %, but only commit after a month of real usage.

**Excluded:** Gemini API usage (billed by Google; the largest variable cost), Meta WhatsApp conversation charges,
domain registration, Route 53 hosted zone (~$0.50/mo if used), egress above the free allowance, taxes other than GST, support plans,
and data transfer during restores.

### EC2 vs Lightsail

| | EC2 t4g.medium | Lightsail 4 GB |
|---|---|---|
| Monthly (₹ incl. GST) | ≈ 2,670 (2,500 without optional logs/snapshots) | ≈ 2,520 |
| IAM **instance role** (no keys on disk; SSM `render-env.sh`, S3 backups, CloudWatch) | **yes** | **no**: needs long-lived access keys on the VM |
| Resize | stop → change type → start (same disk, same IP, ~5–10 min) | snapshot → new bundle → move static IP (new instance; can't shrink disk) |
| Disk | any size; online grow; separate data volume possible | fixed per bundle (+ block storage add-on) |
| Network | security groups, VPC | simple firewall |
| Monitoring | CloudWatch metrics/alarms, auto-recovery | basic metrics/alarms |
| Simplicity | more knobs | simplest console |

**Recommendation: EC2 t4g.medium in ap-south-1 with a 40 GB gp3 root volume, a 2 GB swap file and an Elastic IP.** It costs about the same
as Lightsail, while keeping the instance-role security model (no stored credentials) and in-place resizing. It fits the ₹3,000
target with about 10 % headroom. **Risk:** arm64 image builds are NOT VERIFIED; the base images (python:3.12-slim, postgres:16-alpine,
apache/kafka:3.8.0, caddy, redis, nginx, node) all publish arm64, but no arm64 build was run here. If the first build fails, use
**t3a.medium** (x86, about ₹160/month more). Do not switch architecture after go-live without rebuilding every image.

## 3. Configurable assumptions

| Assumption | Where | Default |
|---|---|---|
| Region | `AWS_REGION` (env), the instance's region | `ap-south-1` |
| VM size | the EC2 instance type (§7 resize) | `t4g.medium` |
| Domain | `PUBLIC_DOMAIN`, `ALLOWED_ORIGINS`, DNS A record | — |
| Image tags | `IMAGE_TAG` (set by `scripts/deploy/deploy.sh` to the git commit), `ZUVO_IMAGE_PREFIX` | `zuvo/<svc>:<commit>` |
| DB / Kafka storage | Docker volumes (root disk), or `ZUVO_DATA_DIR` + `docker-compose.datadir.yml` | Docker volumes |
| Backup bucket / prefix | `BACKUP_S3_URI`, `BACKUP_KMS_KEY_ID`, `BACKUP_DIR`, `BACKUP_KEEP` | — / `/var/backups/zuvo` / 14 |
| App environment | `APP_ENV` | `production` |
| Log retention | local: json-file 5×10 MB per container (`docker-compose.prod.yml`); CloudWatch: `LOG_RETENTION_DAYS` | 30 days |
| Health-check URLs | `HEALTH_URLS` (used by `scripts/deploy/health-check.sh`) | `https://$PUBLIC_DOMAIN/api/v1/health`, `/` |

## 4. Network: security group and ingress

**Production ingress is Caddy on the Elastic IP**, with automatic Let's Encrypt certificates. The Cloudflare tunnel stays a *development*
tool only (`docs/` local E2E). Nothing else is published: gateway, dashboard-api, Postgres, Redis and Kafka have no host ports
in `docker-compose.prod.yml`, and the data services sit on an `internal` Docker network with no internet route.

Security group `zuvo-prod-web`:

| Direction | Port | Source | Why |
|---|---|---|---|
| in | 443/tcp, 443/udp | 0.0.0.0/0, ::/0 | HTTPS + HTTP/3. Meta webhook source IPs are not stable; HMAC signatures (`META_APP_SECRET`) authenticate them |
| in | 80/tcp | 0.0.0.0/0, ::/0 | ACME HTTP-01 challenge + redirect to HTTPS |
| in | 22/tcp | **none** | use **SSM Session Manager** (no open SSH). If you must use SSH: your admin IP `/32` only, and remove it after use |
| out | all | 0.0.0.0/0 | Gemini, Meta Graph API, S3, SSM, Docker Hub, OS updates |

Docker inserts its own iptables rules, so **ufw does not filter published container ports**. The security group is the firewall.
Do not add `ports:` to any service other than Caddy.

## 5. Build the VM (operator runbook; costs money; requires approval)

```bash
# --- once per account (console or CLI). Names are examples.
aws ec2 create-security-group --group-name zuvo-prod-web --description "ZuvoCloud web" --vpc-id <vpc>
aws ec2 authorize-security-group-ingress --group-id <sg> --ip-permissions \
  'IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=0.0.0.0/0}],Ipv6Ranges=[{CidrIpv6=::/0}]' \
  'IpProtocol=udp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=0.0.0.0/0}],Ipv6Ranges=[{CidrIpv6=::/0}]' \
  'IpProtocol=tcp,FromPort=80,ToPort=80,IpRanges=[{CidrIp=0.0.0.0/0}],Ipv6Ranges=[{CidrIpv6=::/0}]'
# instance role "zuvo-prod-vm": AmazonSSMManagedInstanceCore + the inline policies in §6
aws ec2 run-instances --region ap-south-1 --instance-type t4g.medium \
  --image-id resolve:ssm:/aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id \
  --iam-instance-profile Name=zuvo-prod-vm --security-group-ids <sg> --subnet-id <public-subnet> \
  --metadata-options HttpTokens=required,HttpPutResponseHopLimit=1 \
  --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=40,VolumeType=gp3,Encrypted=true,DeleteOnTermination=false}' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=zuvo-prod}]'
aws ec2 allocate-address --domain vpc            # then associate-address with the instance
aws ec2 modify-instance-attribute --instance-id <id> --disable-api-termination
```

`HttpTokens=required` enforces IMDSv2. A hop limit of 1 means containers cannot reach the instance credentials; only the host's
`aws` CLI (backups, render-env) uses the role. `DeleteOnTermination=false` and termination protection guard the data.

On the VM (`aws ssm start-session --target <id>`):

```bash
sudo timedatectl set-timezone UTC
sudo apt-get update && sudo apt-get -y upgrade && sudo apt-get -y install ca-certificates curl git unzip unattended-upgrades
# Docker Engine + compose plugin (>= 2.24 needed for !reset/!override) from Docker's apt repo
curl -fsSL https://get.docker.com | sudo sh && docker compose version
# AWS CLI v2 (arm64 build; use x86_64 on t3a)
curl -sSLo /tmp/awscli.zip https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip && unzip -q /tmp/awscli.zip -d /tmp && sudo /tmp/aws/install
# 2 GB swap: absorbs short peaks instead of OOM kills; low swappiness keeps it a safety net
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab && echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/90-zuvo.conf && sudo sysctl --system
# service user and directories
sudo useradd -m -s /bin/bash -G docker zuvo
sudo install -d -o zuvo -g zuvo -m 750 /opt/zuvo /var/backups/zuvo /var/log/zuvo
sudo -iu zuvo git clone <your-git-remote> /opt/zuvo      # read-only deploy key
```

Then put the variables of `deploy/aws/env.production.example` into SSM under `/zuvo/prod/` (SecureString for secrets) and run
`deploy/aws/render-env.sh --region ap-south-1`. Then `python3 scripts/preflight.py --env-file /opt/zuvo/.env --compose` and
deploy with `scripts/deploy/deploy.sh` (Phase 11, `docs/deployment.md`). Keep `META_DRY_RUN=true` until the webhook is verified.

### DNS and HTTPS

1. At your DNS provider, create an A record `app.<domain>` → Elastic IP (TTL 300). Add an AAAA record only if the instance has IPv6. A CAA record
   `0 issue "letsencrypt.org"` is optional but recommended.
2. Wait until `dig +short app.<domain>` returns the Elastic IP **before** the first `up`. Caddy then obtains the certificate itself through
   HTTP-01/TLS-ALPN. Let's Encrypt rate limits failed attempts, so don't start Caddy against a name that doesn't resolve yet.
3. Check with `curl -sI https://app.<domain>/` (308 from http→https; HSTS header) and `curl https://app.<domain>/api/v1/health`.
4. In Meta App → WhatsApp → Configuration, set the callback `https://app.<domain>/api/v1/whatsapp/webhook` and the verify token =
   `META_VERIFY_TOKEN`. Subscribe to `messages`.

Certificates and the ACME account live in the `caddy_data` volume. If it is lost, Caddy simply re-issues them.

## 6. Instance role and S3 backups

Inline policies on role `zuvo-prod-vm`, in addition to `AmazonSSMManagedInstanceCore`:

```json
{ "Version": "2012-10-17", "Statement": [
  { "Sid": "ReadAppConfig", "Effect": "Allow", "Action": "ssm:GetParametersByPath",
    "Resource": "arn:aws:ssm:ap-south-1:<account>:parameter/zuvo/prod" },
  { "Sid": "ReadAppConfigItems", "Effect": "Allow", "Action": "ssm:GetParameters",
    "Resource": "arn:aws:ssm:ap-south-1:<account>:parameter/zuvo/prod/*" },
  { "Sid": "Logs", "Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents", "logs:PutRetentionPolicy"],
    "Resource": "arn:aws:logs:ap-south-1:<account>:log-group:/zuvo/*" }
] }
```

Add the bucket/prefix-scoped S3 + KMS policy from `docs/backup-and-restore.md` §3. SecureString parameters encrypted with the default
`aws/ssm` key need no extra KMS statement; with a customer-managed key, add `kms:Decrypt` on that key.

Bucket `zuvo-backups-<account>` (same region): Block Public Access, versioning, default SSE-KMS (`alias/zuvo-backup`), and a lifecycle rule
that expires `prod/db/` objects after 30 days and noncurrent versions after 7 days. Then install the cron file from `docs/backup-and-restore.md` §4.
Test it once by hand:
`sudo -iu zuvo /opt/zuvo/scripts/backup/backup-db.sh --project zuvo-ai --s3 s3://zuvo-backups-<account>/prod/db --kms-key alias/zuvo-backup`.

## 7. Monitoring: disk and memory

- **Watch script:** `deploy/ops/zuvo-watch.sh` (Phase 12) from cron every 5 min. It checks disk % (`/`, Docker data root, `ZUVO_DATA_DIR`),
  available memory and swap, container health/restarts/OOM kills, backup age and queue metrics. It logs to `/var/log/zuvo/watch.log` and optionally
  sends alerts. Thresholds and alert routing are in `docs/runbooks/`.
- **Free AWS signals:** a CloudWatch alarm on `StatusCheckFailed_System` with the EC2 **recover** action, plus `CPUCreditBalance` < 20 (t4g
  credits: sustained CPU above the 20 % baseline drains credits; enable `unlimited` only knowingly, because it is billed per vCPU-hour).
- **Optional:** the CloudWatch agent publishing `mem_used_percent`, `disk_used_percent` and `swap_used_percent` (≈ $0.30 per metric-month)
  with alarms at 85 %.
- Log volume is capped locally at about 50 MB per container (json-file rotation). Docker images grow with every release, so
  `scripts/deploy/deploy.sh` keeps the last 3 tags and prunes the rest.

## 8. Resize the VM

EC2 (same disk, same Elastic IP, ~5–10 min downtime):

1. `scripts/backup/backup-db.sh` (with `--s3`) and confirm `upload=ok`.
2. `docker compose ... stop` (graceful, Kafka offsets committed), then `aws ec2 stop-instances --instance-ids <id>`.
3. `aws ec2 modify-instance-attribute --instance-id <id> --instance-type t4g.large` (stay on the same architecture: t4g↔m7g/c7g on arm64,
   t3a↔m7a on x86).
4. `aws ec2 start-instances`. Docker brings the stack back (`restart: unless-stopped`), then run `scripts/deploy/health-check.sh`.
5. Optionally raise `mem_limit`s in `docker-compose.prod.yml` (Kafka/Postgres benefit most).

Disk: `aws ec2 modify-volume --volume-id <vol> --size 80` (online), then `sudo growpart /dev/nvme0n1 1 && sudo resize2fs /dev/nvme0n1p1`. EBS
volumes cannot shrink; create a smaller volume and restore from backup instead.

### Move data to a separate volume (optional, later)

Attach a gp3 volume, format and mount it at `/data` (fstab with `nofail`), and prepare `ZUVO_DATA_DIR` as described in
`deploy/aws/docker-compose.datadir.yml`. Then take a backup and stop the stack. Restore into the new location with
`scripts/backup/restore-db.sh --replace` after starting Postgres with the datadir override. Kafka and Redis can start empty
(the outbox and ledger live in Postgres).

## 9. Teardown: protect the data first

Order matters. Each step is reversible until step 6.

1. Run a final backup with `--s3` and confirm `upload=ok`. Download that S3 copy to another machine and run `scripts/backup/verify-backup.sh` on it.
   It must print `manifest match`.
2. Take an EBS snapshot of the root/data volume(s), tagged `zuvo-final-<date>`.
3. `docker compose ... down` (**never `-v`**: volumes stay on the disk).
4. Point the Meta webhook elsewhere or disable it; remove the DNS record.
5. Disable termination protection and terminate the instance. The volumes survive because `DeleteOnTermination=false`, so delete them only after
   the snapshot is `completed` and step 1 has passed.
6. Release the Elastic IP (an unattached IPv4 is still billed at $0.005/h).
7. **Keep** the S3 bucket, its lifecycle rule and the KMS key for at least the retention period. Deleting or disabling the KMS key makes every
   backup unreadable, so schedule key deletion (≥ 30-day waiting period) only after the bucket is empty and no restore can be needed.
8. Delete the SSM parameters `/zuvo/prod/*` and the IAM role last.

## 10. NOT VERIFIED (requires an AWS account and approval)

No AWS resource was created. Not verified: arm64 builds on Graviton; real Let's Encrypt issuance; SSM `render-env.sh` against real
parameters; the S3/KMS/IAM policies; the CloudWatch awslogs driver; the resize procedure; behaviour and memory peaks on an actual 4 GB VM
(Phase 14 gate); and the free egress allowance and KMS key price (not in the retrieved price-list data).
