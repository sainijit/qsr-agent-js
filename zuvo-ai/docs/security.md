# ZuvoCloud — Security (production pilot)

Companion to `docs/production-deployment.md`. Covers public exposure, application security, secrets, and the
historical credential exposure. Tenant isolation (RLS) is in §5 and `tests/integration/test_rls.py`.

## 1. Public exposure
| Port | Who | Notes |
|---|---|---|
| 443/tcp+udp | Internet | Caddy, automatic Let's Encrypt certificate for `PUBLIC_DOMAIN` |
| 80/tcp | Internet | Caddy: ACME HTTP-01 challenge + 308 redirect to HTTPS |
| 22/tcp | **Admin IP only** — or closed entirely with AWS SSM Session Manager (recommended, `deploy/aws/README.md`) |

Nothing else is published (`docker-compose.prod.yml` resets every other `ports:`). Postgres, Redis and Kafka are on the
`internal` network `data`, with no internet route (verified). Internal FastAPI services and their `/docs` are not reachable
from outside; Caddy additionally answers 404 for `/docs`, `/redoc`, `/openapi.json`, the simulator and the dev-login routes.

## 2. Edge controls (Caddy → frontend nginx)
- TLS only, HSTS 1 year; CSP `default-src 'self'` (+ Google Fonts), `frame-ancestors 'none'`, nosniff, `Referrer-Policy`, `Permissions-Policy`; `Server` header removed.
- Request body ≤ 1 MB at both Caddy and nginx (413).
- Rate limits per client IP (nginx `limit_req`, 429): login 10/min (burst 5); Meta webhook 50/s (burst 200); other API 30/s (burst 100).
  Caddy overwrites client-supplied `X-Forwarded-For`, so nginx's `real_ip` sees the true client address.
- Secrets never reach logs: `?access_token=` (SSE) is stripped by Caddy's log filter, nginx logs `$uri` without query,
  and uvicorn access logs redact `access_token`/`token`/`hub.verify_token` (`zuvo_common.logging.install_access_log_redaction`). Verified with canary tokens.

## 3. Application security
| Control | Implementation |
|---|---|
| Meta webhook authenticity | HMAC-SHA256 `X-Hub-Signature-256` with `META_APP_SECRET`, constant-time compare; unsigned → 401. Production refuses `REQUIRE_HMAC_VERIFICATION=false`. |
| Webhook replay/duplicates | Redis wamid dedupe + `messages(tenant_id, wamid)` unique constraint |
| Dashboard auth | Email + password (scrypt N=2^14), JWT HS256 (`iss=zuvocloud`, `exp`, tenant, role), 8 h TTL |
| Dev shortcuts | `AUTH_DEV_LOGIN`, `ENABLE_SIMULATOR` → services **refuse to start** with `APP_ENV=production` (`UnsafeProductionConfig`) |
| Default passwords | `ZUVO_ONBOARD_DEFAULT_PASSWORD` only seeds users that do not exist yet; preflight rejects weak/well-known values. Production: leave unset (bundle users then have **no** usable password) and set per-user passwords with `scripts/deploy/dcp run --rm --no-deps migrator python set_password.py <email>`: prompt or one stdin line (never argv, never logged), ≥ 12 chars, ≥ 5 distinct, not the email; existing active users only; audit `user.password_set` (actor `operator-cli`, email SHA prefix). |
| Authorization | `enforce_tenant` (403 cross-tenant), role checks (`_require`), worker scoping, RLS as the backstop |
| CORS | allow-list `ALLOWED_ORIGINS`; production requires https origins only (preflight + runtime guard) |
| Login abuse | nginx rate limit + security log `auth.login_failed` / `auth.login_ok` (SHA-256 prefix of the email, no PII) |
| Audit trail | `audit_logs` (RLS) for message ingest, sends, approvals, pricing changes, learned-example toggles; `lead_events` for lead edits/assignment/follow-up skips |
| Input validation | Pydantic models on every request body and event payload; UUID validation for path ids |
| Containers | non-root, read-only root FS, `cap_drop: ALL`, `no-new-privileges` (app services) |

**Cookies:** the dashboard does not use cookies; the JWT is kept in `localStorage` and sent as a Bearer header (no CSRF surface).
The trade-off is XSS exposure of the token, mitigated by the strict CSP (`script-src 'self'`, no inline script).

**Known gaps (accepted for the pilot):** no MFA; no server-side token revocation (rotate `JWT_SECRET` to revoke all sessions);
HS256 shared secret across services; no account lockout beyond rate limiting.

## 4. Secrets management
**Rule:** production secrets live only in AWS SSM Parameter Store and in `/opt/zuvo/.env` (mode 600) on the VM.
Never in git, images, compose files, `.env.example`, docs, logs, or shell history.

Chosen: **SSM Parameter Store `SecureString`** (standard tier, AWS-managed KMS key) — free for ≤ 10,000 parameters,
versus Secrets Manager at about $0.40/secret/month (≈ $4/month for 10 secrets) whose rotation features we do not use yet.

```
/zuvo/prod/POSTGRES_PASSWORD        SecureString
/zuvo/prod/ZUVO_APP_DB_PASSWORD     SecureString
/zuvo/prod/JWT_SECRET               SecureString
/zuvo/prod/RAG_SERVICE_TOKEN        SecureString
/zuvo/prod/META_VERIFY_TOKEN        SecureString
/zuvo/prod/META_APP_SECRET          SecureString
/zuvo/prod/META_SYSTEM_USER_TOKEN   SecureString
/zuvo/prod/GEMINI_API_KEY           SecureString
/zuvo/prod/APP_ENV, PUBLIC_DOMAIN, ACME_EMAIL, ALLOWED_ORIGINS, META_DRY_RUN, ...   String
```

- The EC2 **instance role** grants `ssm:GetParametersByPath` on `arn:aws:ssm:<region>:<account>:parameter/zuvo/prod/*` and `kms:Decrypt` (aws/ssm key). No AWS access keys on the VM.
- `deploy/aws/render-env.sh` renders `.env` atomically (mode 600), prints only variable names, and refuses values it cannot quote safely.
- Create values without shell history: `read -rs V && aws ssm put-parameter --name /zuvo/prod/JWT_SECRET --type SecureString --value "$V" --overwrite; unset V`
  (or generate inline: `--value "$(openssl rand -hex 32)"`).
- Then `python3 scripts/preflight.py --env-file /opt/zuvo/.env --compose` — names only.

## 5. Tenant isolation
**Mechanism.** Postgres RLS (`ENABLE` + `FORCE`) on all 23 tenant tables, one `FOR ALL` policy each:
`tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid` (both `USING` and `WITH CHECK`).
Services connect as `zuvo_app`: not superuser, `NOBYPASSRLS`, owns no tables, and has no `DELETE` except on `kb_chunks`.
`zuvo_common.db.tenant_session` sets the tenant with `set_config(..., true)` — **transaction-local** — inside the same
transaction as the queries, so a pooled connection never carries a tenant into the next checkout.

**Fail-closed.** No context or an empty context → the policy compares against `NULL` → zero rows; inserts are rejected.

**Cross-tenant work** never runs without a tenant: the follow-up scheduler, metrics roll-up and RAG indexer loop over tenant ids
from the routing config and open one `tenant_session` per tenant. Dashboard calls use the JWT tenant (`enforce_tenant` → 403
for any other id); `switch-tenant` re-reads memberships from the DB before issuing a new token.

**Evidence:** `scripts/test_rls.sh` (local compose Postgres, synthetic tenants, self-cleaning; refuses `APP_ENV=production`) → 39 passed:
| Brief requirement | Tests |
|---|---|
| A cannot read B | `test_tenant_cannot_read_other_tenant[*]` — 12 tenant tables + `tenants` |
| A cannot modify/delete B | `..._update_or_delete_other_tenant`, `..._insert_into_other_tenant`, `..._move_row_to_other_tenant`, `..._no_delete_on_business_tables` |
| No context fails closed | `test_missing_context_sees_nothing[*]`, `..._cannot_insert`, `test_empty_context_sees_nothing` |
| Background jobs | `test_scheduler_claims_only_its_tenant` (real `Scheduler.claim_due`), `test_rag_indexer_queue_is_tenant_scoped` |
| RAG retrieval | `test_rag_search_never_returns_other_tenant` — query uses tenant B's exact vector and keywords |
| Connection reuse | `test_pooled_connection_does_not_leak_tenant_context` (pool of 1, same backend pid), `test_context_does_not_survive_rollback` |
| Guard rails | `test_app_role_cannot_bypass_rls`, `test_every_tenant_table_has_forced_rls` (fails if a new table forgets RLS) |

Negative control: the same suite run with a superuser as the "app" role → 38 failed, so the tests detect a bypass.

**Limitations (need schema/privilege redesign to remove):**
1. `users` and `memberships` have no RLS (login and tenant switching run before a tenant is known). `zuvo_app` can read every
   user's email and password hash. Fix later: a `SECURITY DEFINER` login function and revoke direct `SELECT`.
2. `zuvo_owner` (migrations) is the Postgres superuser created by the image. Better: a dedicated non-superuser schema owner,
   with the superuser used only for bootstrap.
3. One shared `zuvo_app` role for all services, including zuvo-ai-rag; no per-service least privilege.
4. No dedicated audited admin role; cross-tenant operator queries use `zuvo_owner` via `psql` (see runbooks) and are not audited.
5. Redis keys and Kafka topics are not covered by RLS. Isolation there is by code: `tenant_id` in every envelope, tenant-specific Redis keys
   include the tenant (`zuvo:wamid|status|send:{tenant}:…`; event dedupe uses globally unique event ids), and consumers always re-open a `tenant_session` for the envelope's tenant.

## 6. Historical credential exposure (R5) — ACTION REQUIRED
**Finding (scan prints no values):** git history contains 2 distinct Meta access-token-shaped strings (`EAA…`) in commit `df2f519`.
No such strings exist in the current tracked tree, `.env` is untracked, and no Gemini (`AIza…`) keys appear in history.
Deleting or rewriting a file does **not** remove it from clones, forks, CI caches or GitHub's object store.
**Treat the credentials as compromised.**

Rotation (owner performs; never automated by tooling):
1. Meta Business Settings → System users → the WhatsApp system user → **Revoke** the old token(s); **Generate new token** (`whatsapp_business_messaging`, `whatsapp_business_management`).
2. App Dashboard → Settings → Basic → **Reset App Secret**. The webhook HMAC changes immediately: update `/zuvo/prod/META_APP_SECRET` and redeploy the gateway in the same window.
3. Choose a new `META_VERIFY_TOKEN` (`openssl rand -hex 24`), update it in SSM, redeploy, then re-verify the webhook in the Meta dashboard.
4. `deploy/aws/render-env.sh && python3 scripts/preflight.py --compose && dcp up -d gateway dispatch`.
5. Verify: a signed test message is accepted, and an outbound reply (with `META_DRY_RUN=false`) returns a `wamid`.
6. Optional history clean-up (`git filter-repo`) only **after** rotation and coordinated with every clone owner — it is hygiene, not remediation.

Re-scan (names/commits only): `git log --all -p | grep -Eo 'EAA[A-Za-z0-9]{40,}' | sort -u | wc -l`.

## 7. Security checks before each release
- `python3 scripts/preflight.py --compose` → 0 errors.
- `curl -sI https://$PUBLIC_DOMAIN/ | grep -i strict-transport` present; `curl -s -o /dev/null -w '%{http_code}' -X POST https://$PUBLIC_DOMAIN/api/v1/auth/dev-login` → 404.
- `git grep -nE 'EAA[A-Za-z0-9]{40,}|AIza[0-9A-Za-z_-]{30,}'` → nothing.
