# Load testing and resource validation

Phase 14 of the production-readiness plan. This document gives the plan, how to run it, what was measured on 2026-10-19, and what it
means for VM sizing. **No paid Gemini calls and no real WhatsApp messages are made.** Gemini is replaced by a local mock and
`META_DRY_RUN=true`.

## 1. What is tested

| Scenario | Command (`scripts/loadtest/zuvo_load.py …`) | Question it answers |
|---|---|---|
| idle | `sample --duration 60` | Baseline memory/CPU of the whole stack |
| startup | `sample` while `dcp up -d --force-recreate` | Memory peak while every JVM/Python process starts |
| steady 1/s | `steady --rate 1 --duration 120 --approve` | ~6× the pilot peak; full path including approve → outbound |
| steady 5/s | `steady --rate 5 --duration 60 --approve` | Where does the pipeline saturate? |
| burst | `burst --count 200 --concurrency 20` | Webhook spike (e.g. a broadcast reply storm) |
| Gemini outage | `outage --rate 1 --duration 150 --outage-at 30 --outage-for 60` | No message loss, lag recovery after Gemini returns |
| Gemini errors | `steady --rate 1 --duration 90 --mock-error-rate 0.1` | Transient 503s absorbed by retries |
| dashboard API | `api --count 500 --concurrency 10 --api-rate 25` | API latency under the nginx per-IP limit |
| RAG | `rag --count 200 --concurrency 5 [--tenant <id>]` | `/retrieve` latency (cold and query-cache hit) |

Pilot traffic assumption: 5 tenants × ~200 inbound messages/day, with a busy hour of about 0.17 msg/s in total.

### Components

- `deploy/loadtest/gemini_mock.py`: a stdlib HTTP server that speaks the Gemini REST paths used by `google-genai`
  (`:generateContent`, `:embedContent`, `:batchEmbedContents`):
  - Answers extraction requests with valid structured JSON and draft requests with a short reply.
  - Returns deterministic bag-of-words embeddings, so related texts score above `min_similarity` and RAG returns chunks.
  - `POST /_control` sets `outage`, `latency_ms`, `jitter_ms`, `error_rate`, `reset`.
  - `GET /_stats` returns call counts and latency.
  - Defaults: 700 ms ± 250 ms per generate call and 120 ms per embed call. **These are assumptions, not measured Gemini latencies.**
- `deploy/loadtest/docker-compose.loadtest.yml`: an overlay that adds `gemini-mock` and points ai-router, agent-worker and zuvo-ai-rag at it
  (`GOOGLE_GEMINI_BASE_URL`, `NO_PROXY`).
- `scripts/loadtest/zuvo_load.py`: the driver.
  - Sends signed Meta webhooks through the edge (Caddy → nginx → gateway).
  - Samples `docker stats`, Kafka consumer lag (via `zuvo_common.opsmetrics`) and `pg_stat_activity`.
  - Then follows every message in Postgres to compute latency:
    - persist: webhook to `messages` row;
    - to-agent-run: webhook to the first `agent_runs` row;
    - inbound→Meta-accepted for auto-sent replies;
    - `decided_at`→accepted for approved drafts.
  - Writes JSON (`--out`).

### Safety interlocks (the driver refuses to run otherwise)

- `PUBLIC_DOMAIN=localhost` (override only with `--allow-remote-test-host` on a disposable VM).
- For webhook scenarios: `META_DRY_RUN=true`, and ai-router's `GOOGLE_GEMINI_BASE_URL` points at `gemini-mock`, which must answer.
- Synthetic customer phone numbers use a random per-run prefix. The default line is `PNID_ALPHA_01` (synthetic), never a real Meta number.

## 2. How to run

Run this on a disposable test deployment, never on the production VM.

```bash
export COMPOSE_PROJECT_NAME=zuvo-prodtest ZUVO_ENV_FILE=/path/to/test.env ZUVO_STATE_DIR=/path/to/state \
       ZUVO_COMPOSE_EXTRA=deploy/loadtest/docker-compose.loadtest.yml
scripts/deploy/dcp up -d --wait                       # adds gemini-mock, re-points the AI services
# After switching to/from the mock, embeddings differ: clear the RAG query cache and reindex the test tenant
scripts/deploy/dcp exec -T redis sh -c "redis-cli --scan --pattern 'rag:qemb:*' | xargs -r redis-cli del"
python3 scripts/loadtest/zuvo_load.py steady --rate 1 --duration 120 --approve --out /tmp/zuvo-lt/steady1.json
# ...other scenarios from the table...
# Restore: drop the overlay and recreate the AI services; reindex the tenant with real Gemini embeddings
unset ZUVO_COMPOSE_EXTRA && scripts/deploy/dcp up -d ai-router agent-worker zuvo-ai-rag && docker rm -f "${COMPOSE_PROJECT_NAME}-gemini-mock-1"
```

Test data stays in the database (`wamid.<run_id>.*`, phone prefix per run). Approving drafts with `--approve` trains the tenant's
learning gate (`autonomy_stats`), so later runs auto-send. **Use a throwaway deployment.**

## 3. Results (2026-10-19)

Host: 16 vCPU / 62 GB dev machine running the production compose (`APP_ENV=production`, prod overlay limits). Release
`dd9f021b78fb-dirty1792408330`, schema 0006. Sampling every 5–10 s with `docker stats` (each call takes ~2 s, so short CPU spikes can be missed).

### 3.1 Memory and CPU

| | Stack memory avg / max | Notable |
|---|---|---|
| Idle (60 s) | 1,103 / 1,197 MiB | Kafka 418 MiB; each app service 50–95 MiB; Postgres 40 MiB; 8 pg connections |
| Startup (full recreate) | peak 1,144 MiB | All healthy in 61 s. Kafka 472 MiB at 201 % CPU, kafka-init 195 % CPU (both JVMs) |
| Steady 1 msg/s | 1,163 / 1,229 MiB | Kafka 447 MiB avg; app services ≤ 7 % CPU avg; 18 pg connections |
| Steady 5 msg/s | 1,231 / 1,302 MiB | Kafka max 542 MiB; 19 pg connections |
| Burst 200 | 1,305 / 1,378 MiB | Kafka max 594 MiB; Postgres 95 MiB |
| Highest observed | **1,385 MiB** | During the RAG run, after all webhook runs |

No OOM kills and no container restarts in any run (`docker inspect … OOMKilled/RestartCount`). Postgres peaked at 20 of 100 connections.

**Kafka CPU:** the base compose healthcheck runs `kafka-broker-api-versions.sh` (a new JVM, ~1–2 CPU-seconds) every 10 s, and that was
most of Kafka's idle CPU (≈12 % of a core). `docker-compose.prod.yml` now probes every 5 s only while starting (`start_interval`) and every 30 s
afterwards (`retries: 3`, so it reports unhealthy within 90 s). Measured idle Kafka CPU fell to ≈7 % of a core. Kafka was healthy 8 s after a recreate and the pipeline
processed 9/9 messages afterwards. This matters on t4g instances, where sustained CPU above the 20 %-per-vCPU baseline spends CPU credits.

### 3.2 Inbound pipeline

| Run | Webhook p50 / p99 | Persist p50 / p99 | Webhook → first agent run p50 / p95 | Result |
|---|---|---|---|---|
| Steady 1/s, 119 msgs | 12.5 / 16.4 ms | 13.0 / 17.2 ms | **1.8 / 2.9 s** | 119/119 processed; lag ≤ 4; drained 9 s after load |
| Steady 5/s, 299 msgs | 12.1 / 15.7 ms | 12.6 / 30.3 ms | 68 / 134 s | ai-router lag to ~390; drained 149 s after load |
| Steady 5/s, ai-router ×3 | — | — | 74 / 140 s | No improvement (see 3.3) |
| Burst 200 in 0.2 s | 20.8 / 35.9 ms | 1.6 / 3.2 s | 68 / 133 s | 200 × 200 OK (fits the nginx burst of 200); drained 149 s after load |
| 10 % Gemini 503s, 1/s | — | — | 3.5 / 5.4 s | 89/89 `EXTRACTED`; 7 injected errors retried |

At 5/s, 26 of 299 messages had no agent run. They are all `GENERAL_CHAT` greetings for conversations the business had already
answered, and the agent intentionally greets only new conversations (`agent_worker/main.py`). They are not lost messages.

### 3.3 Throughput limit and why

Every consumer handles **one record at a time** and commits after the handler (`zuvo_common/bus.py`). Events are keyed by
`tenant_id:worker_id` (`Envelope.key`), so one tenant's traffic is on one partition and is processed in order by one consumer.
ai-router throughput per key is therefore about 1 / Gemini extraction latency: **≈1.4 msg/s with the 700 ms mock**.
Adding ai-router replicas only helps when several tenants or workers are busy at the same time. With a single tenant (this test),
3 replicas gave the same result, because one partition held all the messages.

- Pilot peak (≈0.17 msg/s across all tenants) is about 8× below this limit, even with no replicas.
- If real Gemini extraction is slower (for example 1.5 s), the per-tenant limit drops to ~0.6 msg/s. That is still above the pilot peak, but a
  broadcast reply storm on one tenant will queue. Messages are delayed, not lost (burst: 200/200 processed, 149 s to drain).
- If needed later: key by `tenant_id:customer_id` (keeps per-conversation order, spreads one tenant over 12 partitions), then add ai-router
  and agent-worker replicas. This is a design change to ordering and **was not made** in this phase.

### 3.4 Outbound (dry-run)

| Run | Path | Latency p50 / p95 | Result |
|---|---|---|---|
| Steady 1/s `--approve` | `POST /approvals/{id}/decide` | 54 / 109 ms | 82/82 HTTP 200 |
| | approval `decided_at` → Meta accepted (dry-run) | 1.1 / 1.7 s | 82/82 `ACCEPTED` |
| Steady 5/s `--approve` | approval → accepted | 0.76 / 1.1 s | 47/47 `ACCEPTED` |
| Steady 5/s (auto-send after learning) | inbound webhook → Meta accepted | 50 / 134 s | 263/263 `ACCEPTED`; latency is ai-router queueing (3.3) |

### 3.5 Gemini outage (60 s, at 1 msg/s)

- All 149 webhooks returned 200 and all 149 messages were persisted. **No message was lost.**
- ai-router lag grew to 105 (extraction retries with back-off during the outage), then returned to 0 about 70 s after Gemini recovered.
- 19 messages that arrived during the outage ended as `AI_PENDING` (retries exhausted) and were counted in the dashboard pending queue.
  8 got an approval card (`low_confidence`) and 11 had no agent run. **They are not reprocessed automatically after Gemini
  recovers.** Operators must handle them; see `docs/runbooks/gemini-errors.md`. This matches the documented design (HLD §failure modes) and is listed as a gap.

### 3.6 Dashboard API and RAG

| Run | Result |
|---|---|
| API, 500 requests at 25 r/s, 10 concurrent | 500/500 OK. All p50 15 ms, p95 34 ms, p99 62 ms. Slowest: `/dashboard/today` p95 44 ms |
| API, 500 unpaced from one IP | 137 OK and 363 HTTP 429 (nginx 30 r/s, burst 100) — limiter works; real users come from many IPs |
| RAG `/retrieve`, 200 cold queries, 5 concurrent | 200/200 OK. p50 132 ms, p95 348 ms. Embed (mock 120 ms ± jitter) dominates; pgvector search p95 5 ms |
| RAG `/retrieve`, 200 cached queries | 200/200 OK. p50 11 ms, p95 27 ms (`rag:qemb:*` query-cache hits) |

## 4. Sizing conclusion

- Peak observed memory is **1.38 GiB** under every scenario, against a gate of 3.0 GiB on a 4 GB VM. There were no OOM kills.
- **EC2 t4g.medium (2 vCPU / 4 GB) with 2 GB swap is enough for the pilot.** 2 GB instances remain rejected: idle is already
  1.1 GiB, and startup runs two JVMs (Kafka + kafka-init) at about 2 cores.
- CPU: app services stay at single-digit % at 1 msg/s. Kafka is the largest steady consumer, reduced by the healthcheck change above.
  Watch `CPUCreditBalance` (the alarm in `deploy/aws/README.md` §7).
- Existing alerts already cover resource and backlog problems: `deploy/ops/zuvo-watch.sh` checks memory/swap, disk, container OOM/restarts,
  and Kafka lag (`docs/runbooks/kafka-consumer-lag.md`, `container-crash-oom.md`).

## 5. Limitations and NOT VERIFIED

- **Not run on a real 4 GB / 2 vCPU VM.** The numbers come from a 16-vCPU host, where CPU contention and swap behaviour differ. Repeat
  `steady --rate 1` and `burst` on the AWS VM before go-live (deploy/aws/README.md §1 gate).
- Gemini latency is assumed (700 ms generate, 120 ms embed). Real Flash-Lite latency, quotas and 429 behaviour are not measured.
- Retrieval *quality* is meaningless under the mock: only the latency and the code path are exercised.
- Single client IP: nginx rate limits shape the burst and API tests differently from real Meta and browser traffic.
- `dmesg` OOM-killer logs were not read (no sudo). Only the container `OOMKilled` flags were checked.
- `docker stats` sampling every 5–10 s can miss short CPU spikes.
