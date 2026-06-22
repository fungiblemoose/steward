# Roadmap

Living backlog. Checked = done and tested.

## Phase 1 — Skeleton + mock cluster ✅
- [x] Config system (pydantic-settings, env-driven)
- [x] SQLite schema (metrics / checks / events / actions / app_state)
- [x] Mock Proxmox simulator with 3-node fixture + load injector
- [x] Collector loop persisting metrics + in-memory ring buffer
- [x] Built-in node-CPU-pressure check producing events
- [x] FastAPI `/api/health`, `/api/state`, `/api/metrics`, `/api/events`

## Phase 2 — Checks + rule engine ✅
- [x] Full check schema + registry (a check is data)
- [x] All built-in checks (cpu, mem, vm-down, storage, quorum)
- [x] Deterministic rule engine, pure functions, per-target cooldowns
- [x] Checks CRUD over the API (+ enable/disable/edit/delete)
- [x] Unit tests for each built-in + cooldown + disabled

## Phase 3 — Actions + guardrails (dry-run) ✅
- [x] Action executor: migrate / power / balloon / notify
- [x] Guardrails in order: kill switch, dry-run, allow-list, cooldown +
      rate limit, approval gating, full audit
- [x] Deterministic migration planner (target selection)
- [x] Simulator mutates the fake cluster (migration visibly moves a VM)
- [x] Approval queue + reject + audit trail over the API
- [x] Exhaustive guardrail unit tests

## Phase 4 — LLM layer ✅
- [x] Pluggable OpenAI-compatible client (Ollama-ready) + fake for tests
- [x] NL→check with schema validation → disabled, reviewable check
- [x] Grounded state Q&A from the live snapshot
- [x] Alert explanation
- [x] Graceful degradation when no LLM endpoint configured
- [x] All async, never in the collector loop

## Phase 5 — Hardening + polish (in progress)
- [x] Frontend SPA (dashboard, checks, actions, events, chat, kill switch)
- [x] Dockerfile (multi-stage) + docker-compose
- [x] Secret-scan hook + CI
- [x] README quickstart
- [x] Retention/prune wired into the loop
- [ ] Structured logging polish + in-UI log viewer (basic version shipped)
- [ ] Screenshots / demo gif in README
- [ ] Test coverage report in CI

## Stretch (pull from here next)
- [ ] Historical charts on the dashboard (series API exists; UI charts TODO)
- [ ] "What changed?" diff between two timestamps
- [ ] Predictive pressure (EWMA/trend warn-before-threshold)
- [ ] Migration planner v2 (bin-packing across the cluster) + LLM narration
- [ ] Check templates library + import/export JSON
- [ ] Multi-cluster support
- [ ] RBAC (viewer vs operator)
- [ ] `--demo` mode with scripted incidents
- [ ] Pluggable backends beyond Proxmox behind the same protocol
- [ ] Webhook/event bus for external subscribers
