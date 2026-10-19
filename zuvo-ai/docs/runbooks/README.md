# Runbooks and alerting

| # | Runbook | Triggered by (watch finding key) |
|---|---|---|
| 1 | [application-unavailable](application-unavailable.md) | `edge:health`, `container:<svc>`, `metrics:collect` |
| 2 | [high-error-rate](high-error-rate.md) | tenant reports / Logs Insights query (no automatic alarm, see below) |
| 3 | [kafka-consumer-lag](kafka-consumer-lag.md) | `lag:*`, `lagage:*`, `metric:zuvo_outbox_*`, `metric:zuvo_followups_stuck`, `collect:kafka` |
| 4 | [dead-letter-growth](dead-letter-growth.md) | `dlq:<topic>` (new records since the last check) |
| 5 | [postgres-down-or-disk-full](postgres-down-or-disk-full.md) | `disk:*`, `collect:postgres`, `metric:pg_connections`, `container:postgres` |
| 6 | [redis-unavailable](redis-unavailable.md) | `container:redis`, gateway readyz |
| 7 | [gemini-errors](gemini-errors.md) | `metric:zuvo_agent_latency_p95_ms`, `ratio:ai_failed`, `ratio:agent_outcomes`, `metric:ai_cost` |
| 8 | [rag-indexing](rag-indexing.md) | `metric:zuvo_rag_*` |
| 9 | [outbound-unknown](outbound-unknown.md) | `metric:zuvo_outbound_unknown*`, `metric:zuvo_outbound_sending_stuck` |
| 10 | [meta-webhook-failures](meta-webhook-failures.md) | `metric:zuvo_outbound_pending_retry`, `ratio:outbound_failed`, health-check smoke |
| 11 | [container-crash-oom](container-crash-oom.md) | `oom:<svc>`, `restart:<svc>`, `host:memory`, `host:swap` |
| 12 | [backup-failure](backup-failure.md) | `backup:age`, `backup:unverified` |
| 13 | [restore-dr](restore-dr.md) | operator decision |
| 14 | [failed-deployment](failed-deployment.md) | `deploy.sh` exit ≠ 0 |
| 15 | [security-incident](security-incident.md) | report / discovery |

Setup order on a new VM (alerting is step 13): `deploy/aws/README.md` §0. Load baselines for judging lag and memory: `docs/load-testing.md`.

Severity: **critical** = act now (customers affected or data at risk). **warning** = act during working hours.

## Alerting: `deploy/ops/zuvo-watch.sh`

There is no Prometheus/Alertmanager (RAM/cost decision, `docs/observability.md` §6). One host script evaluates every rule
(`deploy/ops/watch_eval.py`, unit-tested in `tests/unit/test_watch.py`) from:
- host disk, memory and swap;
- container state, health, OOM and restarts;
- the edge `/api/v1/health`;
- newest verified backup age;
- dashboard-api ops metrics.

**Install (on the VM, as user `zuvo`):**
```bash
# 1. a real destination. Without one, the script prints "no alert destination configured" and exits 3. Nothing pretends to alert.
#    SNS (email/SMS): aws sns create-topic --name zuvo-alerts; aws sns subscribe --topic-arn <arn> --protocol email --notification-endpoint ops@...
#    (confirm the subscription email; the instance role needs sns:Publish on that topic ARN only)
echo 'ZUVO_ALERT_SNS_TOPIC_ARN=arn:aws:sns:ap-south-1:<account>:zuvo-alerts' >> /opt/zuvo/.env
#    and/or a Slack / Google Chat incoming webhook (a secret: store it in SSM /zuvo/prod/ZUVO_ALERT_WEBHOOK_URL)
# 2. optional: ZUVO_AI_DAILY_BUDGET_INR=50   BACKUP_MAX_AGE_HOURS=8   ZUVO_ALERT_REMIND_HOURS=6
# 3. cron, every 5 minutes
( crontab -l; echo '*/5 * * * * cd /opt/zuvo && deploy/ops/zuvo-watch.sh >>/var/log/zuvo/watch.log 2>&1' ) | crontab -
```

Behavior:
- **Consecutive checks**: noisy rules (container down, lag, memory, edge) alert only after 2–3 consecutive 5-minute runs.
- **De-duplication**: one message per finding, a reminder every `ZUVO_ALERT_REMIND_HOURS`, an immediate re-alert on escalation from warning to critical, and a `RESOLVED` message when it clears.
- **DLQ**: alerts on *new* records, not on the 7-day standing total.
- **During a deploy/rollback** (deploy lock held): container, edge and metrics-availability checks are suppressed. Backup and disk checks continue.
- **Failed notification**: exit 4; the alert is retried on the next run.
- **Content**: messages contain service and metric names, counts and runbook links only. Never payloads, phone numbers or secrets.

Exit codes: 0 ok, 1 warnings, 2 critical, 3 findings without a destination, 4 notification failed.

**Who watches the watcher**: if the VM or cron dies, nothing alerts. Add one external check outside the VM:
- a CloudWatch alarm on `StatusCheckFailed` → the same SNS topic (free tier: 10 alarms), and
- an uptime check on `https://<domain>/api/v1/health` (Route 53 health check ≈ $0.50/month, or a free external pinger).

**HTTP error rate**: there is no automatic rule, because nginx logs are not aggregated on the host. With the awslogs override, a CloudWatch Logs
metric filter on `/zuvo/frontend` (`{ $.status >= 500 }`) plus an alarm (> 20 per 5 min) → SNS gives one. Until it is configured, this is checked through
tenant reports and the Logs Insights query in `docs/observability.md` §4.

### Evidence (prodtest, 2026-10-19)
- Healthy stack: `OK no findings`, about 5 s per run.
- Stopped `followup-scheduler` + stale-backup threshold, with a local capture webhook:
  - run 1: backup critical sent; container not yet (needs 2 runs).
  - run 2: container critical sent.
  - run 3: nothing sent (de-duplicated).
  - after recovery: `RESOLVED backup:age, container:followup-scheduler` sent.
- Unreachable webhook: exit 4 each run, retried. With the deploy lock held: container checks suppressed, `OK`.
- NOT VERIFIED: real SNS publish and email delivery, cron on the VM, the external uptime check and CloudWatch alarms.

## Operator status view (internal dashboard)

The operator dashboard is deliberately a **command, not a web page**: `deploy/ops/zuvo-watch.sh --status` (read-only; does not notify
or advance alert state), plus `scripts/deploy/health-check.sh`, `scripts/deploy/rollback.sh --list` and the opsmetrics JSON.
- **Authorization**: SSH or SSM Session Manager access to the VM (IAM-controlled, audited by CloudTrail). There is no new network surface.
  `/internal/metrics` is not routed by nginx/Caddy, so it is unreachable from the internet.
- It is separate from the tenant dashboard: tenants never see cross-tenant numbers. The opsmetrics collector runs as the owner role, inside the
  dashboard-api container, and is not exposed through any tenant API.
- When more than one operator needs a browser view, the cheapest real option is a CloudWatch dashboard fed by the CloudWatch agent plus the
  awslogs metric filters (IAM-authorized). A public `/ops` page inside the tenant app is intentionally not built.
