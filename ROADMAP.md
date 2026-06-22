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
- [x] In-UI log viewer (ring-buffer handler + Logs tab)
- [ ] Screenshots / demo gif in README
- [ ] Test coverage report in CI

## Phase 6 — Tier 0: autonomous live-migration balancer ✅
See [`AGENTIC_SRE_PLAN.md`](AGENTIC_SRE_PLAN.md). Deterministic, no LLM, free.
- [x] `imbalance()` / `blended_imbalance()` + greedy `suggest_balancing_migrations()`
      (pure `balancer/policy.py`, fully unit-tested)
- [x] `builtin.autonomous_balancer` check (`auto_execute=True`, ships disabled — opt-in)
- [x] Runtime balancer step (threshold + trend + settle gating) routing moves
      through the existing guarded executor; no LLM
- [x] Pre-execute live target re-validation (refuse a target that isn't online)
- [x] Migration-settle pacing (don't pile a move on a just-initiated one)
- [x] `cpu_cores` on NodeMetric so CPU effect of a move is simulable
- [x] `/api/balancer/simulate` (show would-be moves) + dashboard "Cluster balance" gauge

## Phase 7 — Tier 2: escalate unresolved incidents to Claude Code (in progress)
See [`AGENTIC_SRE_PLAN.md`](AGENTIC_SRE_PLAN.md). Fires rarely → cheap.
- [x] Incident dedup/aging (`incidents.py` `IncidentTracker`: occurrences + age +
      cooldown + ttl; pure & unit-tested)
- [x] `WebhookEscalator` (`escalate.py`) + `STEWARD_ESCALATION_*` config + factory
- [x] Incident payload (incident + recent events + live snapshot + guardrail note)
- [x] Runtime `_run_escalation` step on the poll loop; off unless webhook set
- [ ] Worked example: a receiving Claude Code agent (cron/CI) that reads the API
      and *proposes* into the approval queue (operator-side wiring + sample)

## Tier 1 — local agentic investigator (DEFERRED — needs hardware)
Blocked on memory: 16 GB Mac Mini already runs Compel (~9 GB). Seams preserved.
- [ ] `container_exec` probe/tool (`pct exec`, read-only, `STEWARD_ALLOW_CONTAINER_EXEC`)
- [ ] Bounded ReAct investigator on the existing LLM client seam (local MLX/Ollama)

## Stretch
- [x] Historical charts on the dashboard (per-node CPU/mem trend via series API)
- [x] Predictive pressure (EWMA + linear forecast warn-before-threshold)
- [x] Check import/export as JSON
- [x] `--demo` mode with scripted incidents
- [ ] "What changed?" diff between two timestamps
- [ ] ~~Migration planner v2 (bin-packing)~~ → promoted to **Phase 6**
- [ ] Check templates library (curated starter sets)
- [ ] Multi-cluster support
- [ ] RBAC (viewer vs operator)
- [ ] Pluggable backends beyond Proxmox behind the same protocol
- [ ] Webhook/event bus for external subscribers
