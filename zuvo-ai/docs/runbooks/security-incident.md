# Runbook: Suspected tenant data isolation breach or credential exposure

> Conventions: run on the VM from `/opt/zuvo` as the deploy user. `D=scripts/deploy/dcp` (compose pinned to the current
> release). `M="$D exec -T dashboard-api python -m zuvo_common.opsmetrics"`. Never use `down -v`. Never paste secrets,
> payloads or phone numbers into tickets or chat.

## Symptoms
- A tenant sees another tenant's data, a secret appears in logs, git, chat or a screenshot, or there is unexpected admin activity or unknown logins.

## Immediate actions (do not wait for root cause)
1. Contain:
   - Credential exposed: rotate it now (`docs/security.md` §6 for Meta, §4 for SSM values). Rotating `JWT_SECRET` invalidates all sessions.
   - Cross-tenant data: stop the affected path (`$D stop dashboard-api` makes the dashboard unavailable, while webhooks continue).
2. Preserve evidence: `$D logs --since 24h > /root/incident-$(date +%s).log` (contains no secrets, thanks to redaction). Note the times, tenants and request_ids.

## First diagnostics
```bash
$D exec -T postgres psql -U zuvo_owner -d zuvo_db -c "select relname, relrowsecurity, relforcerowsecurity from pg_class where relname in ('leads','messages','conversations','kb_documents')"
$D logs --since 24h dashboard-api | grep -E '"status": (401|403)' | tail -50
$D exec -T postgres psql -U zuvo_owner -d zuvo_db -c "select created_at, actor, event_type, entity_type, tenant_id from audit_logs order by created_at desc limit 50"
```
- RLS must be enabled **and forced** on tenant tables (`docs/security.md` §5). Re-run `scripts/test_rls.sh` against a non-production copy.

## Recovery
- Fix the cause, release through `deploy.sh`, and confirm with the RLS test suite.

## Requires approval (owner/leadership)
- Notifying affected tenants and, if personal data was exposed, the regulator (India DPDP Act; follow legal advice).
- Revoking tenant or user access, rewriting git history.

## Verify
Rotated credentials work (signed test webhook accepted, outbound send returns a wamid), old credentials are rejected, the RLS suite passes, and a post-incident note is written.
