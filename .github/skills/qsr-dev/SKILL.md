---
name: qsr-dev
description: Act as a senior/staff software engineer for the qsr-agent-js (QSR Store Agent) repository. Use this for any design, implementation, review, or debugging work that touches Hermes orchestration, MCP domain services, autonomy/event decisions, the operator UI, skills, or deployment (Docker/OVMS). Encodes the repository's high- and low-level architecture and conventions so new work stays consistent with them.
---

# QSR Store Agent — Engineering Skill

This repository is a reusable **edge-agent pattern** for quick-service
restaurants: a conversational/autonomy agent (Hermes) that reasons with a
local LLM (Qwen3-8B via OpenVINO Model Server) and calls out to independently
owned **MCP services** that hold all real restaurant data, actions, policy,
and audit evidence. Treat this as the authoritative mental model before
proposing any change.

## High-Level Design (HLD)

```text
Operator -> Hermes -> OVMS/Qwen -> MCP service -> domain system
         <- answer  <- tool use  <- typed result <- policy/audit
```

Event-driven (autonomy) path:

```text
Event producer -> POST /autonomy/events (Operator UI webhook)
  -> durable SQLite queue -> background worker
  -> Hermes selects skill(s) + MCP context reads
  -> decision: no-action explanation OR pending proposal
  -> operator approves/rejects in UI -> MCP action (policy-gated)
```

### Responsibility boundaries (never blur these)

| Component | Owns | Must not own |
|---|---|---|
| Agent runtime (Hermes) | Conversation, tool registration, skill loading, cross-domain orchestration | Restaurant source data or domain authorization |
| OVMS/Qwen | Reasoning, tool selection, answer synthesis | Tool execution or trusted state |
| MCP service | Domain reads, actions, validation, policy, audit data | Cross-domain conversation policy |
| Domain skill (`qsr-skills/*/SKILL.md`) | Question routing, field interpretation, answer/action procedure | Live values, credentials, enforceable authorization |
| `AGENTS.md` | Stable rules spanning every domain | Detailed per-service field mappings |
| `autonomy/` | Event policy, capability catalog, approval workflow | Direct MCP transport details (delegates to `mcp.py`/`ToolClient`) |

A domain service is the only source of truth for its data. The agent/skills
layer never invents, caches-as-fact, or silently reuses stale operational
values — it always re-reads via the context tool when freshness matters.

## Repository Map (low-level)

```text
services/weather/                 Weather MCP service (simulator or Open-Meteo), events, Dockerfile
tests/mcp-services/
  service_base.py                 FastMCP service contract: QsrService, PolicyGate, GateLevel, tool-call JSONL log
  service_launcher.py             Picks one domain service per process via QSR_SERVICE env var
  kiosk_server.py                 Kiosk domain: queue, wait time, staffing, menu, recent activity
  order_accuracy_server.py        Order-accuracy domain: station mismatches, confidence, remakes
  test_services.py                FastMCP in-process client tests per service
agent-config/hermes/
  config.example.yaml             Reusable local Hermes runtime template (model, tools, skills dirs)
  remote-mcp.example.yaml         Remote MCP endpoint registration template
autonomy/                         Event-driven decision engine used by the Operator UI
  registry.py                     AutonomyRegistry/ToolRouter: wires MCP clients + EventPolicy objects
  controller.py                   AutonomyController: runs read rounds + produces decisions
  event_agent.py                  CapabilityCatalog, Capability/Skill objects, Hermes-driven assessment loop
  event_menu_policy.py            Example EventPolicy (weather/queue -> bounded menu changes)
  store.py                        ProposalStore: durable SQLite queue, dedupe, proposal expiry
  worker.py                       Background FIFO worker draining the event queue
  advisor.py                      Hermes CLI driver (bounded rounds/reads, structured output parsing)
  mcp.py                          ToolClient implementation(s) for calling MCP tools
  input_guardrail.py              Input validation ahead of the advisor
  service_capabilities.py         Helper to turn MCP tool discovery into Capability objects
operator-ui/
  app.py                          FastAPI app: chat passthrough, /autonomy/events webhook, approvals API
  static/                         Chat UI, connected-service status, Autonomy decisions panel
qsr-skills/<name>/SKILL.md        Hermes-loaded domain routing/interpretation (kiosk-operations,
                                   order-accuracy, event-menu-decisions, suspicious-activity)
docs/                             architecture.md, adding-a-service.md, autonomy.md, setup.md, index.md
scripts/setup.sh, scripts/uninstall.sh   Idempotent local install/verify/teardown
Makefile, docker-compose.yaml     make up/down/logs/status/weather; REGISTRY vs source build
AGENTS.md                         Cross-domain Hermes rules (this is Hermes' system prompt material)
```

### Core abstractions to reuse, not reinvent

- **`QsrService` (`service_base.py`)**: every MCP service is built from this.
  `read_tool(...)` registers a plain read; `act_tool(name, level, ...)`
  registers a state-changing tool behind a `GateLevel`
  (`AUTOMATIC` / `NOTIFY` / `NEEDS_APPROVAL` / `BLOCKED`). `call_action` is the
  **only** path to executing an action — there is no bypass. Every tool call
  is JSONL-logged (`tool_called` / `tool_succeeded` / `tool_failed`) excluding
  credentials. Transport is selected by `QSR_MCP_TRANSPORT`
  (`stdio` default, `streamable-http`/`http`, or `sse`).
- **`PolicyGate`**: deterministic allow-list + rate limiter. The model never
  decides whether an action runs; it only proposes arguments.
- **`AutonomyRegistry` / `EventPolicy` (`autonomy/registry.py`,
  `event_agent.py`)**: new event types or domains register a
  `register_autonomy(registry)` entry point. Capabilities require explicit
  read dependencies and owner validators; a capability's input schema comes
  from live MCP discovery, not hand-written duplicates.
- **`ProposalStore`**: durable, at-least-once event queue with dedupe by
  `event_id` and 5-minute proposal expiry. Approval re-validates permissions,
  schema, and fresh required context — never trust a stale read.

## Conventions and hard rules

1. **Data/action ownership stays in the MCP service.** Skills and
   `AGENTS.md` describe routing/interpretation only; they must never embed
   live values, credentials, or a parallel source of truth.
2. **Every state-changing tool is gated.** New `act_tool`s must declare a
   `GateLevel` and go through `PolicyGate`. A diagnostic question never
   implies authorization; explicit operator approval (chat or Autonomy UI)
   is required before calling an action tool.
3. **One broad context tool per domain.** `get_<domain>_context` is the
   default read for general/complex questions; split into narrow reads only
   when payload size, latency, authorization, or update cadence requires it.
4. **Schemas carry meaning.** Every schema should include units, time
   windows, confidence semantics, stable IDs, `schema_version`, `source`, and
   `observed_at` where applicable. Preserve these fields end-to-end in
   answers and proposals.
5. **Placeholder honesty.** Current service data is simulated. Any result
   with `"placeholder": true` must be reported to the user as simulated —
   never implied to be a real production effect.
6. **No fixed event->action mapping.** Autonomy event handling is
   capability/skill driven (`event_agent.py` + `qsr-skills/*`), not a
   hardcoded switch statement. Add capabilities/skills; do not hardcode new
   `if event_type == ...` branches.
7. **Remote vs local transport.** Local dev uses `command`/`args` (stdio).
   Production/remote services use `url` + `transport: streamable-http` behind
   TLS and bearer/mTLS auth. Never configure both `url` and `command` for the
   same MCP server entry.
8. **Keep `qsr-skills/*/SKILL.md` free of live data.** Skills map intents to
   tools/fields and state answer rules; they never hardcode a current
   queue count, price, or similar fact.

## Adding or changing a domain service (end-to-end checklist)

Follow `docs/adding-a-service.md` for the full walkthrough. Summary:

1. Create `services/<domain>/service.py` (or extend
   `tests/mcp-services/<domain>_server.py` for simulations) using `QsrService`
   from `service_base.py`. Define one broad `get_<domain>_context` read tool
   and narrow `act_tool`s with explicit `GateLevel`s and JSON Schemas.
2. Register it: add to `platform_toolsets` and `mcp_servers` in
   `~/.hermes/config.yaml` (local: `command`/`args`/`env`; remote: `url` +
   `transport: streamable-http` + auth headers). Never hardcode secrets in
   YAML — reference env vars.
3. Add `qsr-skills/<domain>/SKILL.md` with frontmatter (`name`, `description`,
   `version`, `platforms`, `metadata.hermes.tags`) and a Tool Routing table
   (intent -> tool -> fields -> answer rule). Model it on
   `qsr-skills/kiosk-operations/SKILL.md`.
4. If the domain should trigger autonomous assessment, add a
   `register_autonomy(registry)` entry point: register `Capability` objects
   (with read dependencies and owner validators) and `EventPolicy`/`Skill`
   objects in `autonomy/`, following `event_menu_policy.py` as the template.
   POST events to the Operator UI `/autonomy/events` webhook with a globally
   unique `event_id`.
5. Verify with the ladder in `docs/architecture.md#verification-ladder`:
   unit-test the service function + policy gate without a model, start with
   `QSR_MCP_TRANSPORT=streamable-http`, run `hermes mcp test <service>`, ask a
   simple and a complex question, confirm the service observed `tools/call`,
   test a denied and an approved action, and test one cross-domain question
   with a service intentionally down (Hermes must report partial coverage,
   never fabricate the missing domain).
6. Add/extend tests under `tests/mcp-services/test_services.py` style
   (FastMCP in-process `Client` against `service.build()`), covering context
   shape, action denial/approval, and gate transitions.

## Review checklist for any change in this repo

- Does the change keep live operational data inside the owning MCP service
  (never duplicated into a skill, `AGENTS.md`, or agent runtime code)?
- Is every new or modified action tool behind a `GateLevel` and
  `PolicyGate.evaluate`, with no direct call path around it?
- Are schemas still carrying `source`, `observed_at`, units, and stable IDs?
- Does a skill only change routing/interpretation text, not live values?
- Are placeholder/simulated results still clearly reported as such?
- For autonomy changes: are capabilities/skills additive (new
  `register_autonomy`) rather than new hardcoded event-type branches? Does
  removing a read still correctly cascade to dependent actions/skills (see
  `AutonomyRegistry.register_event` / `CapabilityCatalog.validate`)?
- For transport changes: is `url` XOR `command` set per MCP server, and is a
  remote endpoint behind TLS + auth before replacing a working stdio entry?
- Are new env vars documented (README table, `docs/autonomy.md`
  Configuration table, or `.env.example`) and given safe local defaults?
- Did you run the smallest relevant test target (e.g.
  `tests/mcp-services/test_services.py`, or a targeted `unittest`/`pytest`
  invocation) rather than a full rebuild, unless the change is
  cross-cutting?

## When designing something new

1. State the responsibility boundary first: which existing component
   (agent runtime, MCP service, skill, or autonomy registry) owns the new
   behavior, per the table above.
2. Prefer extending an existing MCP service/skill over adding a new one
   unless the domain is genuinely distinct (separate data owner, separate
   authorization, separate update cadence).
3. Define the MCP tool contract (name, JSON Schema, gate level) before
   writing the implementation.
4. Keep diffs surgical. This is a reference/integration-contract stack with
   simulated data behind real production-shaped contracts — avoid
   speculative abstractions, and don't let a simulation shortcut leak into
   the contract (schema, gating, logging) that a real service must satisfy.

## Key docs to consult (read before large changes)

- `docs/architecture.md` — ownership, trust boundaries, verification ladder.
- `docs/adding-a-service.md` — full new-domain procedure with code samples.
- `docs/autonomy.md` — event queue semantics, approval flow, extension model,
  configuration variables.
- `AGENTS.md` — the cross-domain rules actually loaded into the agent.
