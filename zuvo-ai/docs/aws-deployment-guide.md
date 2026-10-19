# ZuvoCloud on AWS — build and deploy guide (pilot, 2–5 tenants)

A step-by-step guide from an empty AWS account to a live WhatsApp assistant on one VM.
Reference material behind each step: `deploy/aws/README.md` (sizing, costs, IAM, resize, teardown),
`docs/deployment.md` (releases, rollback), `docs/backup-and-restore.md`, `docs/runbooks/`.

> **Status:** every script used here was rehearsed locally in production mode (including a first install on empty
> volumes). Nothing has been run in a real AWS account yet: Let's Encrypt, SSM, S3/KMS, SNS and an arm64 build are
> **NOT VERIFIED**. Steps marked 💰 create billable resources.

---

## 1. Which machine to choose

### What the stack needs (measured)

The 13 long-running containers (Caddy, frontend, gateway, dashboard-api, 6 Kafka consumers, zuvo-ai-rag, Postgres+pgvector,
Redis, Kafka) use about **1.2 GiB RAM at idle** and peaked at **1.38 GiB** during load tests (1 and 5 msg/s, 200-message
burst, Gemini outage). CPU is low because all AI runs at Google (Gemini); Kafka is the biggest consumer (~0.55 GiB).
Images take about 3 GB of disk; the database is small (< 100 MB for the demo tenants).

| Size | Verdict |
|---|---|
| 1–2 GB RAM | **No.** Kafka plus Postgres alone do not fit; OOM kills |
| **4 GB RAM / 2 vCPU + 2 GB swap** | **Pilot size.** ~2.5 GiB headroom over the measured peak |
| 8 GB RAM / 2 vCPU | When the 4 GB load gate fails, or above ~5 busy tenants |

### Options compared (ap-south-1 Mumbai, on-demand, ₹ incl. 18 % GST, ₹86/$; re-check prices before ordering)

| Option | vCPU / RAM | ≈ ₹/month | Notes |
|---|---|---|---|
| **EC2 t4g.medium (ARM, Graviton) — recommended** | 2 / 4 GB | **2,670** (≈ 2,500 without optional logs/snapshots) | cheapest EC2; instance role, in-place resize |
| EC2 t3a.medium (x86) | 2 / 4 GB | 2,830 | fallback if the ARM build fails; same steps, x86 AWS CLI |
| Lightsail 4 GB | 2 / 4 GB | 2,520 | slightly cheaper, but **no instance role** → long-lived AWS keys on the VM; resize = new instance |
| EC2 t4g.large | 2 / 8 GB | 4,330 | the upgrade path (stop → change type → start, ~10 min) |

Not included: Gemini usage (largest variable cost, billed by Google), Meta conversation charges, domain.

**Choose EC2 `t4g.medium`** in `ap-south-1`, Ubuntu 24.04 arm64, 40 GB encrypted gp3 disk, Elastic IP, 2 GB swap file.

Why not the alternatives:
- **t3/t4g "burstable" CPU credits:** fine here; idle CPU is a few % of a core, below the 20 % baseline. Watch `CPUCreditBalance` (§11).
- **Kubernetes / ECS / RDS / MSK:** each managed piece costs more than the whole VM at this scale. Revisit beyond ~20 tenants.
- **ARM risk:** all base images publish arm64, but no arm64 build has been run yet. If step 8 fails to build, terminate and use
  `t3a.medium`. Never switch architecture after go-live without rebuilding all images.

---

## 2. What you need before starting

- [ ] AWS account with billing set up; an admin user with the AWS CLI configured **on your laptop** (`aws configure`)
- [ ] A domain where you can add DNS records (e.g. `app.yourdomain.in`)
- [ ] Meta developer app with WhatsApp: **phone number ID**, **app secret**, a **permanent system-user token**
- [ ] Gemini API key (Google AI Studio), ideally with a budget alert in Google Cloud
- [ ] The code in a private git repo, plus a **read-only deploy key** for the VM
- [ ] For each tenant: `tenants/<slug>/config.yaml` with its real Meta phone number ID and the owner's email
      (copy `tenants/_template/`; the email is the dashboard login). Commit it: tenant files are built into the images.
- [ ] An email address for alerts and Let's Encrypt notices

Set these shell variables on your laptop for the commands below:

```bash
export AWS_REGION=ap-south-1
export ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
export DOMAIN=app.yourdomain.in
```

---

## 3. 💰 Network: security group (the firewall)

```bash
VPC=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
SG=$(aws ec2 create-security-group --group-name zuvo-prod-web --description "ZuvoCloud web" --vpc-id $VPC --query GroupId --output text)
aws ec2 authorize-security-group-ingress --group-id $SG --ip-permissions \
  'IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=0.0.0.0/0}],Ipv6Ranges=[{CidrIpv6=::/0}]' \
  'IpProtocol=udp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=0.0.0.0/0}],Ipv6Ranges=[{CidrIpv6=::/0}]' \
  'IpProtocol=tcp,FromPort=80,ToPort=80,IpRanges=[{CidrIp=0.0.0.0/0}],Ipv6Ranges=[{CidrIpv6=::/0}]'
```

- 443 = HTTPS (+HTTP/3), 80 = Let's Encrypt challenge and redirect. **Port 22 stays closed**: you connect with SSM Session Manager.
- Only Caddy publishes ports. Docker bypasses `ufw`, so the security group is the real firewall.
- Meta's webhook IPs are not fixed; requests are authenticated by HMAC signature (`META_APP_SECRET`) instead.

---

## 4. 💰 Backups bucket, encryption key, alert topic

```bash
aws kms create-key --description "zuvo backups" --query KeyMetadata.KeyId --output text   # note the key id
aws kms create-alias --alias-name alias/zuvo-backup --target-key-id <key-id>

BUCKET=zuvo-backups-$ACCOUNT
aws s3api create-bucket --bucket $BUCKET --create-bucket-configuration LocationConstraint=$AWS_REGION
aws s3api put-public-access-block --bucket $BUCKET --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-versioning --bucket $BUCKET --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption --bucket $BUCKET --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"aws:kms","KMSMasterKeyID":"alias/zuvo-backup"},"BucketKeyEnabled":true}]}'
aws s3api put-bucket-lifecycle-configuration --bucket $BUCKET --lifecycle-configuration \
  '{"Rules":[{"ID":"expire-db","Status":"Enabled","Filter":{"Prefix":"prod/db/"},"Expiration":{"Days":30},"NoncurrentVersionExpiration":{"NoncurrentDays":7}}]}'

TOPIC=$(aws sns create-topic --name zuvo-alerts --query TopicArn --output text)
aws sns subscribe --topic-arn $TOPIC --protocol email --notification-endpoint ops@yourdomain.in   # click the confirmation email
```

---

## 5. 💰 Instance role (how the VM gets permissions without stored keys)

Create role `zuvo-prod-vm` (trusted entity: EC2) and instance profile of the same name:

```bash
aws iam create-role --role-name zuvo-prod-vm --assume-role-policy-document \
  '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam attach-role-policy --role-name zuvo-prod-vm --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
aws iam create-instance-profile --instance-profile-name zuvo-prod-vm
aws iam add-role-to-instance-profile --instance-profile-name zuvo-prod-vm --role-name zuvo-prod-vm
```

Add inline policies (`aws iam put-role-policy --role-name zuvo-prod-vm --policy-name <name> --policy-document file://<file>.json`):

1. **Config + logs:** the JSON in `deploy/aws/README.md` §6 (read SSM `/zuvo/prod/*`, write CloudWatch `/zuvo/*`).
2. **Backups:** the S3/KMS policy in `docs/backup-and-restore.md` §3 (bucket/prefix-scoped put/get/list, KMS on `alias/zuvo-backup`).
3. **Alerts:** `{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"sns:Publish","Resource":"<TOPIC ARN>"}]}`

---

## 6. 💰 Launch the VM

```bash
SUBNET=$(aws ec2 describe-subnets --filters Name=vpc-id,Values=$VPC Name=default-for-az,Values=true --query 'Subnets[0].SubnetId' --output text)
ID=$(aws ec2 run-instances --instance-type t4g.medium \
  --image-id resolve:ssm:/aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id \
  --iam-instance-profile Name=zuvo-prod-vm --security-group-ids $SG --subnet-id $SUBNET \
  --metadata-options HttpTokens=required,HttpPutResponseHopLimit=1 \
  --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=40,VolumeType=gp3,Encrypted=true,DeleteOnTermination=false}' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=zuvo-prod}]' --query 'Instances[0].InstanceId' --output text)
aws ec2 wait instance-running --instance-ids $ID
EIP=$(aws ec2 allocate-address --domain vpc --query AllocationId --output text)
aws ec2 associate-address --instance-id $ID --allocation-id $EIP
aws ec2 describe-addresses --allocation-ids $EIP --query 'Addresses[0].PublicIp' --output text   # the Elastic IP
aws ec2 modify-instance-attribute --instance-id $ID --disable-api-termination
```

- For **t3a.medium** use the `amd64` AMI path and `--instance-type t3a.medium`.
- IMDSv2 with hop limit 1: containers cannot steal the instance credentials; only host scripts use the role.
- `DeleteOnTermination=false` + termination protection: the data disk survives an accidental terminate.

Connect (needs the Session Manager plugin on your laptop): `aws ssm start-session --target $ID`

---

## 7. Prepare the VM (inside the SSM session)

```bash
sudo timedatectl set-timezone UTC
sudo apt-get update && sudo apt-get -y upgrade
sudo apt-get -y install ca-certificates curl git unzip unattended-upgrades python3
curl -fsSL https://get.docker.com | sudo sh && docker compose version            # must be ≥ 2.24
curl -sSLo /tmp/awscli.zip https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip  # x86: ...-linux-x86_64.zip
unzip -q /tmp/awscli.zip -d /tmp && sudo /tmp/aws/install

# 2 GB swap = safety net against OOM kills
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/90-zuvo.conf && sudo sysctl --system

# service user and directories
sudo useradd -m -s /bin/bash -G docker zuvo
sudo install -d -o zuvo -g zuvo -m 750 /opt/zuvo /var/backups/zuvo /var/log/zuvo
```

Get the code with a read-only deploy key:

```bash
sudo install -d -o zuvo -g zuvo -m 750 /srv/src
sudo -iu zuvo
ssh-keygen -t ed25519 -N '' -f ~/.ssh/deploy_key && cat ~/.ssh/deploy_key.pub     # add as a read-only deploy key on GitHub
printf 'Host github.com\n  IdentityFile ~/.ssh/deploy_key\n' >> ~/.ssh/config
git clone git@github.com:<owner>/<repo>.git /srv/src/repo
exit
```

The scripts, cron entries and this guide use `/opt/zuvo` as the zuvo-ai project directory:

```bash
sudo rmdir /opt/zuvo
sudo ln -s /srv/src/repo/zuvo-ai /opt/zuvo      # zuvo-ai is a subfolder (e.g. qsr-agent-js/zuvo-ai)
# sudo ln -s /srv/src/repo /opt/zuvo            # if the repo root is zuvo-ai itself
```

Run every later VM command as `zuvo` (`sudo -iu zuvo`).

---

## 8. Configuration (secrets live in SSM, never in git)

Every variable in `deploy/aws/env.production.example` becomes one SSM parameter: `/zuvo/prod/<NAME>`.
Do this from your laptop (values never touch the VM's shell history):

```bash
put() { aws ssm put-parameter --name "/zuvo/prod/$1" --value "$2" --type "${3:-String}" --overwrite >/dev/null; }
sec() { put "$1" "$2" SecureString; }

# generated secrets
sec POSTGRES_PASSWORD "$(openssl rand -hex 32)"
sec ZUVO_APP_DB_PASSWORD "$(openssl rand -hex 32)"
sec JWT_SECRET "$(openssl rand -hex 32)"
sec RAG_SERVICE_TOKEN "$(openssl rand -hex 32)"
sec META_VERIFY_TOKEN "$(openssl rand -hex 16)"       # you will type this into Meta later: aws ssm get-parameter --with-decryption
# secrets from providers (read -s keeps them off the screen and out of history)
read -rs -p "META_APP_SECRET: " v && sec META_APP_SECRET "$v"; echo
read -rs -p "META_SYSTEM_USER_TOKEN: " v && sec META_SYSTEM_USER_TOKEN "$v"; echo
read -rs -p "GEMINI_API_KEY: " v && sec GEMINI_API_KEY "$v"; echo; unset v

# plain settings
put APP_ENV production;            put AWS_REGION $AWS_REGION
put PUBLIC_DOMAIN $DOMAIN;         put ACME_EMAIL ops@yourdomain.in
put ALLOWED_ORIGINS https://$DOMAIN
put HEALTH_URLS "https://$DOMAIN/api/v1/health https://$DOMAIN/"
put HTTP_PORT 80;                  put HTTPS_PORT 443;   put ZUVO_IMAGE_PREFIX zuvo
put AUTH_DEV_LOGIN false;          put ENABLE_SIMULATOR false;   put REQUIRE_HMAC_VERIFICATION true
put META_DRY_RUN true                                   # stays true until §12
put ZUVO_GEMINI_MODEL gemini-3.5-flash-lite
put GEMINI_TIMEOUT_SECONDS 20;     put GEMINI_MAX_ATTEMPTS 3;    put RAG_TIMEOUT_SECONDS 2
put BACKUP_DIR /var/backups/zuvo;  put BACKUP_KEEP 14
put BACKUP_S3_URI s3://zuvo-backups-$ACCOUNT/prod/db;   put BACKUP_KMS_KEY_ID alias/zuvo-backup
put ZUVO_ALERT_SNS_TOPIC_ARN arn:aws:sns:$AWS_REGION:$ACCOUNT:zuvo-alerts
put BACKUP_MAX_AGE_HOURS 8;        put ZUVO_AI_DAILY_BUDGET_INR 50
put NO_PROXY localhost,127.0.0.1
```

Do **not** set `ZUVO_ONBOARD_DEFAULT_PASSWORD` (preflight rejects weak values; passwords are set per user in §10)
or `IMAGE_TAG` (the deploy script sets it). Compare your list with `env.production.example` for anything new.

On the VM, as `zuvo`:

```bash
cd /opt/zuvo && deploy/aws/render-env.sh --region ap-south-1     # writes /opt/zuvo/.env, mode 600, prints names only
```

Re-run `render-env.sh` whenever you change a parameter, then redeploy (§9).

---

## 9. DNS, then build and deploy

**DNS first.** At your DNS provider create `A  app.yourdomain.in → <Elastic IP>` (TTL 300). Wait until it resolves:

```bash
dig +short app.yourdomain.in        # must print the Elastic IP before you continue
```

Caddy requests the Let's Encrypt certificate on first start; if the name does not resolve yet the request fails and
Let's Encrypt rate-limits retries.

**Build and deploy** (as `zuvo` in `/opt/zuvo`):

```bash
git fetch --tags && git checkout --detach <release-tag-or-commit>    # a clean, committed version
scripts/deploy/preflight.sh                                          # must end with 0 error(s)
scripts/deploy/deploy.sh                                             # 5–15 min the first time (image builds)
```

What `deploy.sh` does: lock → preflight → **build the 11 application images on the VM** (tag = git commit) → start
Postgres/Redis/Kafka → (no backup on the first install: the database is empty) → run migrations and load the tenant
bundles from `tenants/` → start services in order, waiting for each group to be healthy → health check → record the release.
Exit codes: 2 preflight · 3 build · 4 backup · 5 migration · 6 health · 7 another deploy running.

Images are built on the VM; there is no registry. Do not publish these images to a public registry: they contain tenant
configuration (phone numbers, emails, prices, knowledge).

**Verify** (set `DOMAIN=app.yourdomain.in` in this shell):

```bash
curl -sI http://$DOMAIN/ | head -1                 # 308 → https
curl -fsS https://$DOMAIN/api/v1/health            # HTTP 200
scripts/deploy/smoke-test.sh                       # "smoke-test: all checks passed"
scripts/deploy/dcp ps                              # every service healthy (caddy: Up)
```

If the build fails on ARM (step 1 risk), note the error, terminate the instance (keep nothing), and redo §6 with `t3a.medium`.

---

## 10. First login

Bundle users have no password in production. Set one per user (interactive prompt, ≥ 12 characters):

```bash
scripts/deploy/dcp run --rm --no-deps migrator python set_password.py owner@yourtenant.in
```

Open `https://app.yourdomain.in`, log in, and check the tenant's dashboard, knowledge and pricing.

---

## 11. Backups, alerts, monitoring

**Backups** — create `/etc/cron.d/zuvo-backup` (root-owned, mode 644):

```cron
SHELL=/bin/bash
BACKUP_S3_URI=s3://zuvo-backups-<ACCOUNT>/prod/db
BACKUP_KMS_KEY_ID=alias/zuvo-backup
15 0,6,12,18 * * * zuvo /opt/zuvo/scripts/backup/backup-db.sh --project zuvo-ai --quick-verify >>/var/log/zuvo/backup.log 2>&1
45 3 * * *         zuvo /opt/zuvo/scripts/backup/backup-db.sh --project zuvo-ai              >>/var/log/zuvo/backup.log 2>&1
```

Test once by hand — it must print `upload=ok`:

```bash
/opt/zuvo/scripts/backup/backup-db.sh --project zuvo-ai --s3 s3://zuvo-backups-<ACCOUNT>/prod/db --kms-key alias/zuvo-backup
```

**Alerts** — every 5 minutes (as `zuvo`):

```bash
( crontab -l 2>/dev/null; echo '*/5 * * * * cd /opt/zuvo && deploy/ops/zuvo-watch.sh >>/var/log/zuvo/watch.log 2>&1' ) | crontab -
cd /opt/zuvo && deploy/ops/zuvo-watch.sh --status        # current findings, no alert sent
```

**Outside the VM** (if the VM dies, the cron dies too) — from your laptop:

```bash
aws cloudwatch put-metric-alarm --alarm-name zuvo-status-check --namespace AWS/EC2 --metric-name StatusCheckFailed_System \
  --dimensions Name=InstanceId,Value=$ID --statistic Maximum --period 60 --evaluation-periods 2 --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --alarm-actions arn:aws:automate:$AWS_REGION:ec2:recover $TOPIC
aws cloudwatch put-metric-alarm --alarm-name zuvo-cpu-credits --namespace AWS/EC2 --metric-name CPUCreditBalance \
  --dimensions Name=InstanceId,Value=$ID --statistic Minimum --period 300 --evaluation-periods 3 --threshold 20 \
  --comparison-operator LessThanThreshold --alarm-actions $TOPIC
```

Add a free external uptime check (or Route 53 health check, ~$0.50/month) on `https://app.yourdomain.in/api/v1/health`.

---

## 12. Connect WhatsApp and go live

1. Meta App → WhatsApp → Configuration: callback `https://app.yourdomain.in/api/v1/whatsapp/webhook`,
   verify token = the `META_VERIFY_TOKEN` from SSM. Subscribe to **messages**.
2. Send a WhatsApp message to the business number. It must appear in the dashboard; the AI reply is generated but **not sent**
   (dry run). Check the logs: `scripts/deploy/dcp logs --tail 50 gateway ai-router agent-worker`.
3. **Load gate on this VM** (no paid AI calls; uses the Gemini mock): run `steady --rate 1` and `burst` from
   `docs/load-testing.md` §2. Pass = memory peak < 3.0 GiB, no OOM kills, queue drains. Fail → resize to t4g.large (§13).
4. Go live:
   ```bash
   aws ssm put-parameter --name /zuvo/prod/META_DRY_RUN --value false --type String --overwrite   # laptop
   cd /opt/zuvo && deploy/aws/render-env.sh --region ap-south-1 && scripts/deploy/deploy.sh      # VM
   ```
5. Send another test message and confirm a real reply arrives.

**Before real customers:** rotate the Meta tokens that were exposed in the old git history (`docs/security.md` §6).

---

## 13. Day-2 operations

| Task | Command / doc |
|---|---|
| New release | `git fetch && git checkout --detach <tag> && scripts/deploy/deploy.sh` (takes a verified backup first when migrations are pending) |
| Roll back the app | `scripts/deploy/rollback.sh` (`--list` to see releases; refused if the schema changed) |
| Status / logs | `scripts/deploy/dcp ps`, `scripts/deploy/dcp logs --tail 100 <service>`, `deploy/ops/zuvo-watch.sh --status` |
| Add a tenant | add `tenants/<slug>/` + routing, commit, deploy; then `set_password.py` for its owner |
| Edit knowledge / prices | edit files in `tenants/<slug>/`, commit, deploy (RAG re-indexes within seconds) |
| Restore data | `docs/backup-and-restore.md` §5 (needs approval; never automatic) |
| Something broke | `docs/runbooks/README.md` (15 runbooks) |
| Resize to 8 GB | backup → `dcp stop` → stop instance → `modify-instance-attribute --instance-type t4g.large` → start → `health-check.sh` (~10 min downtime) |
| OS updates | `unattended-upgrades` installs security fixes; reboot in a quiet hour; Docker restarts the stack |

Never run `docker compose down -v` — it deletes the database, Kafka and certificate volumes.
Use `scripts/deploy/dcp` instead of plain `docker compose` on the server.

## 14. Teardown

Follow `deploy/aws/README.md` §9 in order (final verified S3 backup → EBS snapshot → stop → terminate → release Elastic IP;
keep the bucket and KMS key for the retention period).
